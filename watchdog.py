import asyncio
import logging
from functools import partial

logger = logging.getLogger(__name__)

class IdleTimeout(Exception):
    pass

class WatchdogServer():
    def __init__(self, loop = None):
        if loop is None:
            self.loop = asyncio.get_event_loop()
        else:
            self.loop = loop
        self.clients = set()
    
    def start(self):
        self.loop.call_later(1, self.tick)
        return self
    
    def register(self, client):
        client.on_timeout.add_done_callback(partial(self.client_timeout, client = client))
        self.clients.add(client)
    
    def client_timeout(self, exc, client):
        self.clients.discard(client)
    
    def tick(self):
        self.loop.call_later(1, self.tick)
        for i in self.clients.copy():
            if i.timeout <= i.cnt:
                if isinstance(i.result, Exception):
                    i.on_timeout.set_exception(i.result)
                else:
                    i.on_timeout.set_result(i.result)
            else:
                i.cnt += 1

class WatchdogClient:
    def __init__(self, server, timeout, result, loop = None):
        self.server = server
        self.timeout = timeout
        self.result = result
        if loop is None:
            self.on_timeout = asyncio.get_event_loop().create_future()
        else:
            self.on_timeout = loop.create_future()
        self.cnt = 1
    
    def start(self):
        self.server.register(self)
        return self.on_timeout
    
    def reset(self):
        self.cnt = 1
