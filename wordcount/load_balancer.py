"""Phase 3 TCP byte-stream proxy with two dynamic selection policies.

No RPyC parsing, application retries, or worker health monitoring is performed.
"""

import argparse
import asyncio
import json
import logging
import math
import time
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
    metrics_port: int = 18863
    ewma_ms: float | None = None
    completed_queries: int = 0
    metrics_updated: float = 0.0
    sample_age_s: float | None = None
    last_selected: float = 0.0
    metrics_errors: int = 0


class TcpBalancer:
    def __init__(self, backends, connect_timeout=3, session_timeout=120,
                 algorithm="least_connections", poll_interval=1, metrics_enabled=False):
        if not backends:
            raise ValueError("At least one backend is required.")
        if algorithm not in ("least_connections", "estimated_response_time"):
            raise ValueError("Unknown balancing algorithm.")
        self.algorithm=algorithm
        self.poll_interval=poll_interval
        self.metrics_enabled=metrics_enabled
        self.metrics_task=None
        self.selections=0
        self.backends = backends
        self.connect_timeout = connect_timeout
        self.session_timeout = session_timeout
        self.next_index = 0
        self.sessions = set()
        self.listener = None
        self.status_listener = None

    def estimate(self, backend):
        now=time.monotonic()
        def fresh(b):
            return (b.ewma_ms is not None and b.sample_age_s is not None
                    and now-b.metrics_updated<=3*self.poll_interval
                    and b.sample_age_s+now-b.metrics_updated<=15)
        if fresh(backend):return max(.001,backend.ewma_ms)
        # Unknown or old telemetry is not interpreted as an instant response.
        return max([b.ewma_ms for b in self.backends if fresh(b)]+[1.0])

    def score(self, backend):
        return self.estimate(backend)*(backend.active+1)

    def acquire(self):
        # One event loop owns selection and counters, with no intervening await.
        count=len(self.backends)
        self.selections+=1
        tie=lambda i:(i-self.next_index)%count
        if self.algorithm=="least_connections":
            index=min(range(count),key=lambda i:(self.backends[i].active,tie(i)))
        else:
            unknown=[i for i,b in enumerate(self.backends) if b.completed_queries==0 and b.active==0]
            if unknown:
                index=min(unknown,key=lambda i:(self.backends[i].last_selected,tie(i)))
            elif self.selections%10==0:
                # Bounded exploration permits updated measurements on workers
                # that previously looked slow. It can still select a busy worker.
                index=min(range(count),key=lambda i:(self.backends[i].last_selected,tie(i)))
            else:
                index=min(range(count),key=lambda i:(self.score(self.backends[i]),tie(i)))
        backend=self.backends[index]
        backend.active+=1
        backend.selected+=1
        backend.last_selected=time.monotonic()
        self.next_index=(index+1)%count
        return backend

    def release(self, backend):
        if backend.active <= 0:
            raise RuntimeError("Unbalanced connection accounting.")
        backend.active -= 1

    def snapshot(self):
        return {"algorithm": self.algorithm, "worker_health_monitoring": False,
                "metrics_polling": self.metrics_enabled,
                "backends": [{"name": b.name, "active_connections": b.active,
                              "selected_connections": b.selected,
                              "connect_failures": b.connect_failures,
                              "ewma_ms": b.ewma_ms, "completed_queries": b.completed_queries,
                              "metrics_age_s": None if b.metrics_updated==0 else round(time.monotonic()-b.metrics_updated,3),
                              "sample_age_s_at_last_poll": b.sample_age_s,
                              "estimated_score_ms": round(self.score(b),3),
                              "metrics_errors": b.metrics_errors}
                             for b in self.backends]}

    async def poll_backend(self, backend):
        writer=None
        try:
            async with asyncio.timeout(.75):
                reader,writer=await asyncio.open_connection(backend.host,backend.metrics_port,limit=16384)
                writer.write(b"GET /metrics HTTP/1.1\r\nHost: worker\r\nConnection: close\r\n\r\n")
                await writer.drain()
                header=await reader.readuntil(b"\r\n\r\n")
                if b" 200 " not in header.split(b"\r\n",1)[0]:raise ValueError("Metrics HTTP error")
                headers={k.strip().lower():v.strip() for line in header.split(b"\r\n")[1:] if b":" in line for k,v in [line.split(b":",1)]}
                length=int(headers[b"content-length"])
                if not 0<length<=16384:raise ValueError("Invalid metrics length")
                payload=json.loads(await reader.readexactly(length))
                value=payload.get('ewma_ms')
                completed=payload['completed_queries']
                age=payload.get('sample_age_s')
                if payload['server_id']!=backend.name or type(completed) is not int or completed<0:
                    raise ValueError("Invalid worker metrics identity or counter")
                if completed:
                    if (not isinstance(value,(int,float)) or not math.isfinite(value) or value<0
                            or not isinstance(age,(int,float)) or not math.isfinite(age) or age<0):
                        raise ValueError("Invalid query timing")
                backend.ewma_ms=value if completed else None
                backend.completed_queries=completed
                backend.sample_age_s=age if completed else None
                backend.metrics_updated=time.monotonic()
        except (OSError, TimeoutError, ValueError, KeyError, TypeError,
                asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            backend.metrics_errors+=1
            # Failed polling does not exclude a worker in Phase 3.
        finally:
            if writer is not None:
                writer.close()
                try:await asyncio.wait_for(writer.wait_closed(),timeout=1)
                except (OSError, TimeoutError):pass

    async def poll_metrics(self):
        while True:
            await asyncio.gather(*(self.poll_backend(b) for b in self.backends))
            await asyncio.sleep(self.poll_interval)

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
        LOGGER.info(json.dumps({"event": "assigned", "worker": backend.name, "algorithm": self.algorithm,
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
        if self.metrics_enabled:
            self.metrics_task=asyncio.create_task(self.poll_metrics())

    async def close(self):
        if self.metrics_task is not None:
            self.metrics_task.cancel()
            await asyncio.gather(self.metrics_task,return_exceptions=True)
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
    balancer = TcpBalancer(args.backend,algorithm=args.algorithm,metrics_enabled=True)
    await balancer.start(port=args.port, status_port=args.status_port)
    LOGGER.info("%s proxy listening on %s; status on %s", args.algorithm, args.port, args.status_port)
    try:
        await balancer.listener.serve_forever()
    finally:
        await balancer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", action="append", type=parse_backend, required=True)
    parser.add_argument("--algorithm",choices=["least_connections","estimated_response_time"],default="least_connections")
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
