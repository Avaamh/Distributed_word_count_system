"""Test worker timing and estimated-response-time routing decisions."""
import asyncio
import json
import time
import unittest
from urllib.request import ProxyHandler,build_opener
from wordcount.load_balancer import Backend,TcpBalancer
from wordcount.worker_metrics import QueryMetrics,start_metrics

class WorkerMetricsTests(unittest.TestCase):
    def test_ewma_and_active_accounting(self):
        m=QueryMetrics(alpha=.2)
        m.begin();self.assertEqual(m.active,1);m.finish(10)
        m.begin();m.finish(20)
        result=m.snapshot('worker')
        self.assertEqual(result['ewma_ms'],12)
        self.assertEqual(result['completed_queries'],2)
        self.assertEqual(result['active_queries'],0)
    def test_tracking_preserves_return_and_exception_cleanup(self):
        m=QueryMetrics();m.enabled=True
        @m.track
        def function(fail=False):
            self.assertEqual(m.active,1)
            if fail:raise ValueError('test')
            return 47
        self.assertEqual(function(),47)
        with self.assertRaises(ValueError):function(True)
        self.assertEqual(m.completed,2);self.assertEqual(m.active,0)
    def test_disabled_tracking_has_no_samples(self):
        m=QueryMetrics()
        self.assertEqual(m.track(lambda:47)(),47)
        self.assertEqual(m.completed,0)
    def test_metrics_http_endpoint(self):
        m=QueryMetrics();server,thread=start_metrics(m,'worker-1',0,host='127.0.0.1')
        try:
            opener=build_opener(ProxyHandler({}))
            with opener.open(f'http://127.0.0.1:{server.server_port}/metrics',timeout=2) as r:payload=json.load(r)
            self.assertEqual(payload['server_id'],'worker-1')
            self.assertEqual(payload['completed_queries'],0)
        finally:server.shutdown();server.server_close();thread.join(timeout=2)

class ResponseSelectionTests(unittest.TestCase):
    def workers(self):
        now=time.monotonic()
        workers=[Backend('slow','localhost',1),Backend('fast','localhost',2),Backend('other','localhost',3)]
        for worker,elapsed in zip(workers,[80,2,5]):
            worker.ewma_ms=elapsed;worker.completed_queries=10
            worker.metrics_updated=now;worker.sample_age_s=0
        return workers
    def test_prefers_faster_worker_at_equal_connection_counts(self):
        balancer=TcpBalancer(self.workers(),algorithm='estimated_response_time')
        self.assertEqual(balancer.acquire().name,'fast')
    def test_busy_fast_worker_can_lose_to_less_busy_worker(self):
        workers=self.workers();workers[1].active=4
        balancer=TcpBalancer(workers,algorithm='estimated_response_time')
        self.assertEqual(balancer.acquire().name,'other')
    def test_exploration_resamples_previously_slow_worker(self):
        workers=self.workers()
        workers[0].last_selected=1;workers[1].last_selected=2;workers[2].last_selected=3
        balancer=TcpBalancer(workers,algorithm='estimated_response_time');balancer.selections=9
        self.assertEqual(balancer.acquire().name,'slow')
    def test_stale_sample_uses_conservative_fallback(self):
        workers=self.workers();workers[1].metrics_updated-=60
        balancer=TcpBalancer(workers,algorithm='estimated_response_time')
        self.assertEqual(balancer.estimate(workers[1]),80)
    def test_unknown_idle_worker_gets_initial_sample(self):
        workers=self.workers();workers[0].completed_queries=0;workers[0].ewma_ms=None
        balancer=TcpBalancer(workers,algorithm='estimated_response_time')
        self.assertEqual(balancer.acquire().name,'slow')

class PollingTests(unittest.IsolatedAsyncioTestCase):
    async def test_collects_real_worker_metrics(self):
        m=QueryMetrics();m.begin();m.finish(12)
        server,thread=start_metrics(m,'worker-1',0,host='127.0.0.1')
        try:
            worker=Backend('worker-1','127.0.0.1',1,metrics_port=server.server_port)
            balancer=TcpBalancer([worker],algorithm='estimated_response_time')
            await balancer.poll_backend(worker)
            self.assertEqual(worker.ewma_ms,12)
            self.assertEqual(worker.completed_queries,1)
            self.assertEqual(worker.metrics_errors,0)
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close();thread.join(timeout=2)
    async def test_failed_poll_does_not_remove_worker(self):
        worker=Backend('worker-1','127.0.0.1',1,metrics_port=1)
        balancer=TcpBalancer([worker],algorithm='estimated_response_time')
        await balancer.poll_backend(worker)
        self.assertEqual(worker.metrics_errors,1)
        self.assertIs(balancer.acquire(),worker)
