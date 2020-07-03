#!/usr/bin/env python3
import asyncio
import logging
import argparse
import ssl
import functools
import json
import hmac
from urllib.parse import urlparse, parse_qs
from collections import namedtuple
import constants
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

class UdpClient:
    def __init__(self, que, peer_addr):
        self.que = que
        self.peer_addr = peer_addr
        self.transport = None
    
    def connection_made(self, transport):
        self.transport = transport
    
    def datagram_received(self, data, addr):
        if self.peer_addr != addr:
            logger.warning(f'Dropped data from {addr}')
            return
        mv = memoryview(data)
        M = constants.WS_MAX_MSG_SIZE
        for i in range(0, len(data), M):
            self.que.put_nowait(mv[i:i+M])
    
    def error_received(self, exc):
        logger.debug(f"UdpClient.error_received: {repr(exc)}")
    
    def connection_lost(self, exc):
        logger.debug(f"UdpClient.connection_lost: {repr(exc)}")
        self.write_eof_to_que()
    
    def write_eof_to_que(self):
        self.que.put_nowait(None)

class TcpClient(asyncio.Protocol):
    def __init__(self, que, peer_addr):
        self.que = que
        self.peer_addr = peer_addr
        self.transport = None
    
    def connection_made(self, transport):
        self.transport = transport
    
    def data_received(self, data):
        mv = memoryview(data)
        M = constants.WS_MAX_MSG_SIZE
        for i in range(0, len(data), M):
            self.que.put_nowait(mv[i:i+M])
    
    def connection_lost(self, exc):
        logger.debug(f"TcpClient.connection_lost: {repr(exc)}")
        self.write_eof_to_que()
    
    def write_eof_to_que(self):
        self.que.put_nowait(None)

async def ws_data_sender(ws, que, watchdog):
    while True:
        if watchdog:
            watchdog.reset()
        data = await que.get()
        if data is None:
            que.task_done()
            return
        await ws.send(data)
        que.task_done()

async def ws_data_receiver(ws, f_write_to_transport, watchdog):
    while True:
        if watchdog:
            watchdog.reset()
        f_write_to_transport(await ws.recv())

def verify_token(expected, received, default):
    if expected is None:
        return default
    if received is None:
        return False
    return hmac.compare_digest(expected, received)

async def ws_server(ws, path, routes, idle_timeout):
    peername = ws.transport.get_extra_info("peername")
    logger.debug(f'New Websocket connection from {peername}, path={path}')
    path = urlparse(path)
    try:
        received_token = parse_qs(path.query)['t'][0]
    except Exception:
        received_token = None
    try:
        upstream_proto, upstream_addr, expected_token = routes[path.path]
    except KeyError:
        logger.info(f'Rejected Websocket connection from {peername}: no route')
        return
    try:
        if not verify_token(expected_token, received_token, default=True):
            logger.info(f'Rejected Websocket connection from {peername}: password mismatch')
            return
        logger.info(f'Accepted Websocket connection from {peername}')
        que = asyncio.Queue()
        loop = asyncio.get_running_loop()
        if upstream_proto == 'udp':
            transport, _ = await loop.create_datagram_endpoint(
                lambda: UdpClient(que, upstream_addr),
                remote_addr=upstream_addr)
            f_write_to_transport = transport.sendto
        else:
            transport, _ = await loop.create_connection(
                lambda: TcpClient(que, upstream_addr),
                upstream_addr[0], upstream_addr[1])
            f_write_to_transport = transport.write
    except Exception as e:
        logger.error(repr(e), exc_info = True)
        return
    tasks = []
    try:
        if idle_timeout:
            watchdog = Watchdog(idle_timeout, ConnIdleTimeout(f"Connection from {peername} has idled"))
            tasks.append(asyncio.create_task(watchdog.start()))
        else:
            watchdog = None
        tasks.append(asyncio.create_task(ws_data_receiver(ws, f_write_to_transport, watchdog)))
        tasks.append(asyncio.create_task(ws_data_sender(ws, que, watchdog)))
        done, _ = await asyncio.wait(tasks, return_when = 'FIRST_COMPLETED')
        for i in done:
            exc = i.exception()
            if exc:
                raise exc
    except (ConnIdleTimeout,
            websockets.exceptions.ConnectionClosedOK) as e:
        logger.info(repr(e))
    except Exception as e:
        logger.error(repr(e))
    finally:
        for t in tasks:
            t.cancel()
        transport.close()
        logger.debug(f"Connection from {peername} is terminated")

Route = namedtuple('Route', ['protocol', 'endpoint', 'token'])

def parse_routes(routes_json):
    with open(routes_json, 'r') as fi:
        j = json.load(fi)
    ret = dict()
    for k,v in j.items():
        protocol,rest = v['Endpoint'].split('://', maxsplit=1)
        if protocol not in ['tcp', 'udp']:
            raise ValueError(f'Unsupported upstream protocol {protocol}')
        endpoint = rest.split(':', maxsplit=1)
        endpoint[1] = int(endpoint[1])
        token = v.get('Token')
        if token is None:
            logger.warning(f'Path {k} in {routes_json} is not password-protected')
        ret[k] = Route(protocol, tuple(endpoint), token)
    return ret

def main(args):
    local_addr = args.listen.split(':', 1)
    local_addr[1] = int(local_addr[1])
    routes = parse_routes(args.routes)
    ws_server_bound = functools.partial(ws_server, routes=routes, idle_timeout=args.idle_timeout)
    if args.cert:
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_context.options |= ssl.OP_NO_TICKET
        ssl_context.load_cert_chain(args.cert)
        if args.client_ca:
            ssl_context.verify_mode = ssl.CERT_REQUIRED
            ssl_context.load_verify_locations(cafile = args.client_ca)
        ssl_param = {'ssl': ssl_context}
    else:
        logger.warning('Secure connection is disabled')
        ssl_param = dict()
    asyncio.get_event_loop().run_until_complete(
        websockets.serve(ws_server_bound,
                         local_addr[0], local_addr[1],
                         max_size = constants.WS_MAX_MSG_SIZE_COMP, max_queue = None,
                         compression = 'deflate' if args.enable_compress else None,
                         **ssl_param))
    asyncio.get_event_loop().run_forever()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Wstunnel server')
    parser.add_argument('-l', '--listen', type=str, metavar='IP:PORT', required=True, help='Listen address')
    parser.add_argument('-r', '--routes', type=str, metavar='routes.json', required=True, help='File defining paths and upstream servers')
    parser.add_argument('-i', '--idle-timeout', type=int, default=120, help='Seconds to wait before an idle connection being killed')
    parser.add_argument('-s', '--cert', type=str, metavar='server.pem', help='Server certificate in PEM format with private key')
    parser.add_argument('-c', '--client-ca', type=str, metavar='ca.pem', help='Client CA certificates in PEM format to verify against')
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
    logging.getLogger(__name__).setLevel(log_level)
    main(args)
