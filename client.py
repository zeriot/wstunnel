#!/usr/bin/env python3
import asyncio
import logging
import argparse
import ssl
from urllib.parse import urlencode, urlparse, urlunparse
import constants
# https://github.com/aaugustin/websockets
import websockets

import watchdog as wd

logger = logging.getLogger(__name__)

class BaseServer:
    def __init__(self,
                 client,
                 f_write_to_transport,
                 f_conn_lost,
                 uri,
                 certfile,
                 client_cert,
                 idle_timeout,
                 compress,
                 watchdog_server):
        self.client = client
        self.done = False
        self.que = asyncio.Queue()
        if uri.startswith('wss://'):
            ssl_context = ssl.create_default_context(cafile = certfile)
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
            if client_cert:
                ssl_context.load_cert_chain(client_cert)
            ssl_param = {'ssl': ssl_context}
        else:
            ssl_param = dict()
        asyncio.create_task(self.new_client(uri,
                                            ssl_param,
                                            f_write_to_transport,
                                            f_conn_lost,
                                            idle_timeout,
                                            compress,
                                            watchdog_server))
    
    def data_received(self, data):
        self.que.put_nowait(memoryview(data))
    
    def shutdown(self):
        self.done = True
        self.que.put_nowait(None)
    
    async def new_client(self,
                         uri,
                         ssl_param,
                         f_write_to_transport,
                         f_conn_lost,
                         idle_timeout,
                         compress,
                         watchdog_server):
        tasks = []
        try:
            async with websockets.connect(uri,
                                          max_size = constants.WS_MAX_MSG_SIZE_COMP, max_queue = None,
                                          compression = compress,
                                          **ssl_param) as ws:
                if idle_timeout:
                    watchdog = wd.WatchdogClient(watchdog_server,
                                                 idle_timeout,
                                                 wd.IdleTimeout(f"Connection {self.client} has idled"))
                    tasks.append(watchdog.start())
                else:
                    watchdog = None
                tasks.append(asyncio.create_task(self.ws_data_sender(ws, watchdog)))
                tasks.append(asyncio.create_task(self.ws_data_receiver(ws, f_write_to_transport, watchdog)))
                done, _ = await asyncio.wait(tasks, return_when = 'FIRST_COMPLETED')
                for i in done:
                    exc = i.exception()
                    if exc:
                        raise exc
        except (wd.IdleTimeout,
                websockets.exceptions.ConnectionClosedOK) as e:
            logger.info(repr(e))
        except Exception as e:
            logger.error(repr(e), exc_info = True)
        finally:
            for t in tasks:
                t.cancel()
            if not self.done:
                f_conn_lost(self.client)
    
    async def ws_data_sender(self, ws, watchdog):
        M = constants.WS_MAX_MSG_SIZE
        que = self.que
        while True:
            if watchdog:
                watchdog.reset()
            data = await que.get()
            if data is None:
                que.task_done()
                return
            for i in range(0, len(data), M):
                await ws.send(data[i:i+M])
            que.task_done()
    
    async def ws_data_receiver(self, ws, f_write_to_transport, watchdog):
        async for data in ws:
            if watchdog:
                watchdog.reset()
            f_write_to_transport(data, self.client)

class UdpServer:
    def __init__(self, uri, certfile, client_cert, idle_timeout, compress, watchdog_server):
        self.base_servers = dict()
        self.args = [uri, certfile, client_cert, idle_timeout, compress, watchdog_server]
    
    def connection_made(self, transport):
        self.transport = transport
    
    def datagram_received(self, data, addr):
        try:
            base = self.base_servers[addr]
        except KeyError:
            logger.info(f'New UDP connection from {addr}')
            base = self.base_servers[addr] = BaseServer(addr,
                                                        self.transport.sendto,
                                                        self.upstream_lost,
                                                        *self.args
                                                       )
        base.data_received(data)
    
    def write_to_transport(self, data, addr):
        self.transport.sendto(data, addr)
    
    def upstream_lost(self, addr):
        logger.info(f'Upstream connection for UDP client {addr} is gone')
        self.base_servers.pop(addr)

class TcpServer(asyncio.Protocol):
    def __init__(self, uri, certfile, client_cert, idle_timeout, compress, watchdog_server):
        self.args = [uri, certfile, client_cert, idle_timeout, compress, watchdog_server]
        self.peername = None
        self.base = None
        self.transport = None
        super().__init__()
    
    def connection_made(self, transport):
        peername = self.peername = transport.get_extra_info('peername')
        logger.info(f'New TCP connection from {peername}')
        self.transport = transport
        self.base = BaseServer(peername,
                               self.write_to_transport,
                               self.upstream_lost,
                               *self.args
                              )
    
    def data_received(self, data):
        self.base.data_received(data)
    
    def connection_lost(self, exc):
        logger.info(f'TCP connection from {self.peername} is down: {repr(exc)}')
        self.base.shutdown()
    
    def write_to_transport(self, data, addr):
        self.transport.write(data)
    
    def upstream_lost(self, peername):
        self.transport.close()

def get_passwd_from_file(path):
    with open(path, 'r') as fi:
        return fi.readline().rstrip('\r\n')

def update_url_with_passwd(url, passwd):
    url = urlparse(url)
    url = url._replace(query = urlencode({'t': passwd}))
    return urlunparse(url)

async def main(args):
    protocol, local_addr = args.listen.split('://', maxsplit=1)
    local_addr = local_addr.split(':', maxsplit=1)
    local_addr = (local_addr[0], int(local_addr[1]))
    if args.passwd:
        uri = update_url_with_passwd(args.url, get_passwd_from_file(args.passwd))
    else:
        uri = args.url
    if not uri.startswith('wss://'):
        logger.warning('Secure connection is disabled')
    compress = 'deflate' if args.enable_compress else None
    loop = asyncio.get_running_loop()
    watchdog_server = wd.WatchdogServer(loop).start()
    if protocol == 'udp':
        transport, _ = await loop.create_datagram_endpoint(lambda: UdpServer(uri,
                                                                             args.ca_certs,
                                                                             args.client_cert,
                                                                             args.idle_timeout,
                                                                             compress,
                                                                             watchdog_server),
                                                           local_addr = local_addr
                                                          )
        try:
            await loop.create_future() # Serve forever
        finally:
            transport.close()
    else:
        server = await loop.create_server(lambda: TcpServer(uri,
                                                            args.ca_certs,
                                                            args.client_cert,
                                                            args.idle_timeout,
                                                            compress,
                                                            watchdog_server),
                                          local_addr[0], local_addr[1]
                                         )
        async with server:
            await server.serve_forever()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Wstunnel client')
    parser.add_argument('--url', type=str, metavar='ws[s]://HOSTNAME:PORT/PATH', required=True, help='URL')
    parser.add_argument('-l', '--listen', type=str, metavar='(tcp|udp)://IP:PORT', required=True, help='Listen address')
    parser.add_argument('-p', '--passwd', type=str, metavar='FILE', help='File containing one line of password to authenticate to the proxy server')
    parser.add_argument('-i', '--idle-timeout', type=int, default=120, help='Seconds to wait before an idle connection being killed')
    parser.add_argument('-s', '--ca-certs', type=str, metavar='ca.pem', help="Server CA certificates in PEM format to verify against")
    parser.add_argument('-c', '--client-cert', type=str, metavar='client.pem', help="Client certificate in PEM format with private key")
    parser.add_argument('--enable-compress', type=bool, const=True, nargs='?', help='Compress data before sending')
    parser.add_argument('--log-file', type=str, metavar='FILE', help='Log to FILE')
    parser.add_argument('--log-level', type=str, default="info", choices=['debug', 'info', 'error', 'critical'], help='Log level')
    args = parser.parse_args()
    if args.log_level == 'debug':
        log_level = logging.DEBUG
    elif args.log_level == 'error':
        log_level = logging.ERROR
    elif args.log_level == 'critical':
        log_level = logging.CRITICAL
    else:
        log_level = logging.INFO
    logging_config_param = {'format': '%(levelname)s::%(asctime)s::%(filename)s:%(lineno)d::%(message)s',
                            'datefmt': '%Y-%m-%d %H:%M:%S'
                           }
    if args.log_file:
        logging_config_param['filename'] = args.log_file
    logging.basicConfig(**logging_config_param)
    logger.setLevel(log_level)
    wd.logger.setLevel(log_level)
    asyncio.run(main(args))
