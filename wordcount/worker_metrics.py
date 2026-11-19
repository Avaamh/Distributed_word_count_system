"""Optional, thread-safe query-duration telemetry for the local lab."""
import functools
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class QueryMetrics:
    def __init__(self, alpha=.2):
        self.alpha=alpha
        self.enabled=False
        self.delay_ms=0.0
        self.lock=threading.Lock()
        self.active=0
        self.completed=0
        self.ewma_ms=None
        self.last_completion=None

    def begin(self):
        with self.lock:self.active+=1

    def finish(self, elapsed_ms):
        with self.lock:
            self.active-=1
            self.completed+=1
            self.ewma_ms=elapsed_ms if self.ewma_ms is None else self.alpha*elapsed_ms+(1-self.alpha)*self.ewma_ms
            self.last_completion=time.monotonic()

    def snapshot(self, server_id):
        with self.lock:
            return dict(server_id=server_id,active_queries=self.active,
                        completed_queries=self.completed,ewma_ms=self.ewma_ms,
                        sample_age_s=None if self.last_completion is None else time.monotonic()-self.last_completion,
                        artificial_delay_ms=self.delay_ms)

    def track(self, function):
        @functools.wraps(function)
        def wrapped(*args,**kwargs):
            if not self.enabled:return function(*args,**kwargs)
            self.begin()
            start=time.perf_counter()
            try:
                if self.delay_ms:time.sleep(self.delay_ms/1000)
                return function(*args,**kwargs)
            finally:
                self.finish((time.perf_counter()-start)*1000)
        return wrapped


def start_metrics(metrics, server_id, port, delay_ms=0, host='0.0.0.0'):
    if not math.isfinite(delay_ms) or not 0<=delay_ms<=10000:
        raise ValueError('QUERY_DELAY_MS must be between 0 and 10000.')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!='/metrics':
                self.send_error(404);return
            body=json.dumps(metrics.snapshot(server_id)).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Connection','close')
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer((host,port),Handler)
    server.daemon_threads=True
    metrics.enabled=True
    metrics.delay_ms=delay_ms
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    return server,thread
