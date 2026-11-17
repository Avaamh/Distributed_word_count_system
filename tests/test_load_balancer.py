"""Test selection, byte-stream forwarding, cleanup, and real RPyC traffic."""
import asyncio
import json
import threading
import unittest

import rpyc
from rpyc.utils.server import ThreadedServer

from wordcount.client import connect
from wordcount.load_balancer import Backend, TcpBalancer


class SelectionTests(unittest.TestCase):
    def test_selects_least_active_worker(self):
        workers=[Backend('a','localhost',1,active=4),Backend('b','localhost',2,active=1),Backend('c','localhost',3,active=3)]
        balancer=TcpBalancer(workers)
        chosen=balancer.acquire()
        self.assertIs(chosen,workers[1])
        self.assertEqual(chosen.active,2)
        balancer.release(chosen)
        self.assertEqual(chosen.active,1)

    def test_equal_counts_rotate(self):
        workers=[Backend(str(i),'localhost',i+1) for i in range(3)]
        balancer=TcpBalancer(workers)
        selected=[]
        for _ in range(6):
            worker=balancer.acquire()
            selected.append(worker.name)
            balancer.release(worker)
        self.assertEqual(selected,['0','1','2','0','1','2'])

    def test_invalid_accounting_and_empty_pool_are_rejected(self):
        with self.assertRaises(ValueError):TcpBalancer([])
        balancer=TcpBalancer([Backend('a','localhost',1)])
        with self.assertRaises(RuntimeError):balancer.release(balancer.backends[0])


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.listeners=[]
        self.balancers=[]

    async def asyncTearDown(self):
        for balancer in self.balancers:await balancer.close()
        for listener in self.listeners:
            listener.close()
            await listener.wait_closed()

    async def start_proxy(self,workers,**kwargs):
        balancer=TcpBalancer(workers,**kwargs)
        await balancer.start(host='127.0.0.1',port=0,status_port=0)
        self.balancers.append(balancer)
        return balancer,balancer.listener.sockets[0].getsockname()[1]

    async def wait_idle(self,balancer):
        async with asyncio.timeout(3):
            while balancer.sessions:await asyncio.sleep(.01)

    async def test_binary_forwarding_and_half_close(self):
        async def echo(reader,writer):
            data=await reader.read()
            writer.write(data[::-1]);await writer.drain()
            writer.close();await writer.wait_closed()
        listener=await asyncio.start_server(echo,'127.0.0.1',0)
        self.listeners.append(listener)
        worker=Backend('echo','127.0.0.1',listener.sockets[0].getsockname()[1])
        balancer,port=await self.start_proxy([worker])
        reader,writer=await asyncio.open_connection('127.0.0.1',port)
        payload=bytes(range(256))*500
        writer.write(payload);await writer.drain();writer.write_eof()
        received=await asyncio.wait_for(reader.read(),timeout=3)
        self.assertEqual(received,payload[::-1])
        writer.close();await writer.wait_closed()
        await self.wait_idle(balancer)
        self.assertEqual(worker.active,0)

    async def test_failed_backend_releases_connection(self):
        listener=await asyncio.start_server(lambda r,w:None,'127.0.0.1',0)
        port=listener.sockets[0].getsockname()[1]
        listener.close();await listener.wait_closed()
        worker=Backend('offline','127.0.0.1',port)
        balancer,port=await self.start_proxy([worker])
        reader,writer=await asyncio.open_connection('127.0.0.1',port)
        with self.assertLogs('wordcount.balancer',level='WARNING'):
            self.assertEqual(await asyncio.wait_for(reader.read(),timeout=3),b'')
        writer.close();await writer.wait_closed()
        await self.wait_idle(balancer)
        self.assertEqual(worker.active,0)
        self.assertEqual(worker.connect_failures,1)

    async def test_session_limit_closes_idle_connection(self):
        async def consume(reader,writer):
            await reader.read()
            writer.close();await writer.wait_closed()
        listener=await asyncio.start_server(consume,'127.0.0.1',0)
        self.listeners.append(listener)
        worker=Backend('idle','127.0.0.1',listener.sockets[0].getsockname()[1])
        balancer,port=await self.start_proxy([worker],session_timeout=.05)
        reader,writer=await asyncio.open_connection('127.0.0.1',port)
        with self.assertLogs('wordcount.balancer',level='WARNING'):
            self.assertEqual(await asyncio.wait_for(reader.read(),timeout=3),b'')
        writer.close();await writer.wait_closed()
        await self.wait_idle(balancer)
        self.assertEqual(worker.active,0)

    async def test_status_does_not_select_a_worker(self):
        balancer,_=await self.start_proxy([Backend('a','localhost',1)])
        port=balancer.status_listener.sockets[0].getsockname()[1]
        reader,writer=await asyncio.open_connection('127.0.0.1',port)
        writer.write(b'GET /status HTTP/1.1\r\nHost: localhost\r\n\r\n');await writer.drain()
        response=await reader.read()
        body=json.loads(response.split(b'\r\n\r\n',1)[1])
        self.assertEqual(body['backends'][0]['selected_connections'],0)
        writer.close();await writer.wait_closed()

    async def test_rpyc_calls_reach_three_workers(self):
        def service(name):
            class Worker(rpyc.Service):
                def exposed_ping(self):return name
            return Worker
        workers=[];servers=[];threads=[]
        try:
            for index in range(3):
                name=f'worker-{index+1}'
                srv=ThreadedServer(service(name),hostname='127.0.0.1',port=0,auto_register=False)
                thread=threading.Thread(target=srv.start,daemon=True);thread.start()
                servers.append(srv);threads.append(thread)
                workers.append(Backend(name,'127.0.0.1',srv.port))
            balancer,port=await self.start_proxy(workers)
            def ping():
                with connect('127.0.0.1',port) as conn:return conn.root.ping()
            observed=[]
            for _ in range(6):
                observed.append(await asyncio.to_thread(ping))
                await self.wait_idle(balancer)
            self.assertEqual(observed,['worker-1','worker-2','worker-3']*2)
        finally:
            for srv in servers:srv.close()
            for thread in threads:thread.join(timeout=3)


if __name__=='__main__':
    unittest.main()
