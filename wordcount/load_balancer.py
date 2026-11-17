"""Phase 3 TCP byte-stream proxy with least-connections selection.

No RPyC parsing, application retries, or worker health monitoring is performed.
"""

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass

LOGGER = logging.getLogger("wordcount.balancer")


@dataclass
class Backend:
    name: str
    host: str
    port: int
    active: int = 0
    selected: int = 0
    connect_failures: int = 0


class TcpBalancer:
    def __init__(self, backends, connect_timeout=3, session_timeout=120):
        if not backends:
            raise ValueError("At least one backend is required.")
        self.backends = backends
        self.connect_timeout = connect_timeout
        self.session_timeout = session_timeout
        self.next_index = 0
        self.sessions = set()
        self.listener = None
        self.status_listener = None

    def acquire(self):
        # No await occurs between selection and increment: one event loop owns
        # these counters, so another handler cannot select stale counts here.
        count = len(self.backends)
        index = min(range(count), key=lambda i: (
            self.backends[i].active, (i-self.next_index) % count))
        backend = self.backends[index]
        backend.active += 1
        backend.selected += 1
        self.next_index = (index+1) % count
        return backend

    def release(self, backend):
        if backend.active <= 0:
            raise RuntimeError("Unbalanced connection accounting.")
        backend.active -= 1

    def snapshot(self):
        return {"algorithm": "least_connections", "worker_health_monitoring": False,
                "backends": [{"name": b.name, "active_connections": b.active,
                              "selected_connections": b.selected,
                              "connect_failures": b.connect_failures}
                             for b in self.backends]}

    @staticmethod
    async def relay(reader, writer):
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        # Preserve TCP half-close: the other direction can still return data.
        if writer.can_write_eof():
            writer.write_eof()
            await writer.drain()

    async def handle(self, client_reader, client_writer):
        task = asyncio.current_task()
        self.sessions.add(task)
        backend = self.acquire()
        upstream_writer = None
        relays = []
        LOGGER.info(json.dumps({"event": "assigned", "worker": backend.name,
                                "active": {b.name: b.active for b in self.backends}}))
        try:
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(backend.host, backend.port),
                    timeout=self.connect_timeout)
            except (OSError, TimeoutError):
                backend.connect_failures += 1
                raise
            relays = [asyncio.create_task(self.relay(client_reader, upstream_writer)),
                      asyncio.create_task(self.relay(upstream_reader, client_writer))]
            await asyncio.wait_for(asyncio.gather(*relays), timeout=self.session_timeout)
        except (OSError, TimeoutError) as exc:
            LOGGER.warning(json.dumps({"event": "connection_error", "worker": backend.name,
                                       "error": type(exc).__name__}))
        finally:
            for relay in relays:
                relay.cancel()
            if relays:
                await asyncio.gather(*relays, return_exceptions=True)
            for writer in (upstream_writer, client_writer):
                if writer is not None:
                    writer.close()
            for writer in (upstream_writer, client_writer):
                if writer is not None:
                    try:
                        await asyncio.wait_for(writer.wait_closed(), timeout=2)
                    except (OSError, TimeoutError):
                        pass
            self.release(backend)
            self.sessions.discard(task)
            LOGGER.info(json.dumps({"event": "closed", "worker": backend.name,
                                    "active": backend.active}))

    async def status(self, reader, writer):
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
            if header.split(b"\r\n",1)[0] != b"GET /status HTTP/1.1":
                body, status = b'{"error":"not_found"}', b"404 Not Found"
            else:
                body = json.dumps(self.snapshot()).encode()
                status = b"200 OK"
            writer.write(b"HTTP/1.1 "+status+b"\r\nContent-Type: application/json\r\n"
                         b"Connection: close\r\nContent-Length: "+str(len(body)).encode()+b"\r\n\r\n"+body)
            await writer.drain()
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2)
            except (OSError, TimeoutError):
                pass

    async def start(self, host="0.0.0.0", port=18860, status_port=18862):
        self.listener = await asyncio.start_server(self.handle, host, port)
        try:
            self.status_listener = await asyncio.start_server(self.status, host, status_port, limit=8192)
        except Exception:
            self.listener.close()
            await self.listener.wait_closed()
            raise

    async def close(self):
        for listener in (self.listener, self.status_listener):
            if listener is not None:
                listener.close()
                await listener.wait_closed()
        sessions = list(self.sessions)
        for session in sessions:
            session.cancel()
        if sessions:
            await asyncio.gather(*sessions, return_exceptions=True)


def parse_backend(value):
    try:
        name, address = value.split("=",1)
        host, raw_port = address.rsplit(":",1)
        port = int(raw_port)
        if not name or not host or not 1 <= port <= 65535:
            raise ValueError
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected worker-name=hostname:port") from exc
    return Backend(name, host, port)


async def run(args):
    balancer = TcpBalancer(args.backend)
    await balancer.start(port=args.port, status_port=args.status_port)
    LOGGER.info("Least-connections proxy listening on %s; status on %s", args.port, args.status_port)
    try:
        await balancer.listener.serve_forever()
    finally:
        await balancer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", action="append", type=parse_backend, required=True)
    parser.add_argument("--port", type=int, default=18860)
    parser.add_argument("--status-port", type=int, default=18862)
    args = parser.parse_args()
    if len({b.name for b in args.backend}) != len(args.backend):
        parser.error("Backend names must be unique.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
