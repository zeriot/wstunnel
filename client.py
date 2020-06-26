#!/usr/bin/env python3
import asyncio
import logging
import argparse
import ssl
from urllib.parse import urlencode, urlparse, urlunparse
# https://github.com/aaugustin/websockets
import websockets

logger = logging.getLogger(__name__)

class ConnIdleTimeout(Exception):
    pass

class Watchdog:
    def __init__(self, timeout, exc):
        self.timeout = timeout
        self.cnt = 0
        self.exc = exc
    
    def reset(self):
        self.cnt = 0
    
    async def start(self):
        while True:
            await asyncio.sleep(1)
            self.cnt += 1
            if self.cnt == self.timeout:
                raise self.exc

class BaseServer:
    def __init__(self, uri, certfile, client_cert, idle_timeout = None):
        self.uri = uri
        self.idle_timeout = idle_timeout
        self.shutdown = asyncio.get_running_loop().create_future()
        self.transport = None
        if uri.startswith('wss://'):
            ssl_context = ssl.create_default_context(cafile = certfile)
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
            if client_cert:
                ssl_context.load_cert_chain(client_cert)
            self.ssl_param = {'ssl': ssl_context}
        else:
            self.ssl_param = dict()
    
    async def new_client(self, que, peername):
        if self.idle_timeout:
            watchdog = Watchdog(self.idle_timeout, ConnIdleTimeout(f"Connection {peername} has idled"))
        else:
            watchdog = None
        tasks = []
        try:
            async with websockets.connect(self.uri, **self.ssl_param) as ws:
                tasks = [asyncio.create_task(self.ws_data_sender(ws, que, watchdog)),
                         asyncio.create_task(self.ws_data_receiver(ws, peername, watchdog))
                        ]
                if watchdog:
                    tasks.append(asyncio.create_task(watchdog.start()))
                done, _ = await asyncio.wait({self.shutdown, *tasks}, return_when = 'FIRST_COMPLETED')
                exc = done.pop().exception()
                if exc:
                    raise exc
        except ConnIdleTimeout as e:
            logger.info(repr(e))
        except Exception as e:
            logger.error(repr(e))
        finally:
            self.client_lost(peername)
            for t in tasks:
                t.cancel()
    
    async def ws_data_sender(self, ws, que, watchdog):
        while True:
            if watchdog:
                watchdog.reset()
            await ws.send(await que.get())
            que.task_done()
    
    async def ws_data_receiver(self, ws, peername, watchdog):
        while True:
            if watchdog:
                watchdog.reset()
            self.write_to_transport(await ws.recv(), peername)
    
    def write_to_transport(self, data, peername):
        raise NotImplementedError()
    
    def client_lost(self, peername):
        pass

class UdpServer(BaseServer):
    def __init__(self, *args):
        self.que = dict()
        super().__init__(uri, *args)
    
    def connection_made(self, transport):
        self.transport = transport
    
    def datagram_received(self, data, addr):
        if addr in self.que:
            self.que[addr].put_nowait(data)
            return
        logger.info(f'New UDP connection from {addr}')
        que = self.que[addr] = asyncio.Queue()
        que.put_nowait(data)
        asyncio.create_task(self.new_client(que, addr))
    
    def write_to_transport(self, data, addr):
        self.transport.sendto(data, addr)
    
    def client_lost(self, peername):
        self.que.pop(peername)
        logger.debug(f'UDP connection from {peername} is lost')

class ProtocolWrapper(asyncio.Protocol):
    def __init__(self, *l, **d):
        super().__init__()

class TcpServer(BaseServer, ProtocolWrapper):
    def __init__(self, *args):
        super().__init__(*args)
    
    def connection_made(self, transport):
        peername = transport.get_extra_info('peername')
        logger.info(f'New TCP connection from {peername}')
        self.transport = transport
        self.que = asyncio.Queue()
        asyncio.create_task(self.new_client(self.que, peername))
    
    def data_received(self, data):
        self.que.put_nowait(data)
    
    def connection_lost(self, exc):
        self.shutdown.set_result(True)
        logger.debug(f'TcpServer.connection_lost: {repr(exc)}')
    
    def write_to_transport(self, data, addr):
        self.transport.write(data)
    
    def client_lost(self, peername):
        self.transport.close()
        logger.debug(f'TCP connection from {peername} is lost')

def get_passwd_from_file(path):
    with open(path, 'r') as fi:
        return fi.readline().rstrip('\r\n')

def update_url_with_passwd(url, passwd):
    url = urlparse(url)
    url = url._replace(query = urlencode({'t': passwd}))
    return urlunparse(url)

async def main(listen, uri, passwd_file, certfile, client_cert, idle_timeout):
    protocol, local_addr = listen.split('://', maxsplit=1)
    local_addr = local_addr.split(':', maxsplit=1)
    local_addr = (local_addr[0], int(local_addr[1]))
    uri = update_url_with_passwd(uri, get_passwd_from_file(passwd_file))
    if not uri.startswith('wss://'):
        logger.warning('Secure connection is disabled')
    loop = asyncio.get_running_loop()
    if protocol == 'udp':
        transport, _ = await loop.create_datagram_endpoint(lambda: UdpServer(uri, certfile, client_cert, idle_timeout),
                                                           local_addr = local_addr
                                                          )
        try:
            await loop.create_future()
        finally:
            transport.close()
    else:
        server = await loop.create_server(lambda: TcpServer(uri, certfile, client_cert, idle_timeout),
                                          local_addr[0], local_addr[1]
                                         )
        async with server:
            await server.serve_forever()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Wstunnel client')
    parser.add_argument('--url', type=str, required=True, help='URL')
    parser.add_argument('-l', '--listen', type=str, required=True, help='Listen address')
    parser.add_argument('-p', '--passwd', type=str, metavar='FILE', help='File containing one line of password to authenticate to the proxy server')
    parser.add_argument('-i', '--idle-timeout', type=int, default=120, help='Seconds to wait before an idle UDP connection being killed')
    parser.add_argument('-s', '--ca-certs', type=str, metavar='ca.crt', help="Server CA certificates to verify against")
    parser.add_argument('-c', '--client-cert', type=str, metavar='client.pem', help="Client certificate with private key")
    parser.add_argument('--log-file', type=str, metavar='FILE', help='Log to FILE')
    parser.add_argument('--log-level', type=str, default="info", choices=['debug', 'info'], help='Log level')
    args = parser.parse_args()
    log_level = logging.DEBUG if args.log_level == 'debug' else logging.INFO
    logging_config_param = {'format': '%(levelname)s::%(asctime)s::%(name)s::%(filename)s:%(lineno)d::%(message)s',
                            'datefmt': '%Y-%m-%d %H:%M:%S'
                           }
    if args.log_file:
        logging_config_param['filename'] = args.log_file
    logging.basicConfig(**logging_config_param)
    logging.getLogger(__name__).setLevel(log_level)
    asyncio.run(main(args.listen, args.url, args.passwd, args.ca_certs, args.client_cert, args.idle_timeout))
