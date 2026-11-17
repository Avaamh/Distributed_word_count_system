"""Check benchmark accounting and bounded overload behavior."""
import time
import unittest
from unittest.mock import patch
from wordcount.benchmark import percentile,run_load,summarize


class BenchmarkTests(unittest.TestCase):
    def test_nearest_rank_percentile(self):
        self.assertEqual(percentile(list(range(1,101)),99),99)
        self.assertEqual(percentile([7],99),7)
        self.assertIsNone(percentile([],99))

    def test_errors_remain_in_summary(self):
        records=[dict(ok=True,started=1,rpc_ms=2,end_to_end_ms=3,start_lag_ms=0,cache_status='hit',server_id='one'),
                 dict(ok=False,started=None,rpc_ms=None,end_to_end_ms=None,start_lag_ms=None,cache_status='',server_id='')]
        summary=summarize(records,2,'cached',10,1)
        self.assertEqual(summary['offered'],2)
        self.assertEqual(summary['sent'],1)
        self.assertEqual(summary['failed'],1)
        self.assertEqual(summary['failure_percent'],50)
        self.assertEqual(summary['achieved_rps'],.5)
        self.assertEqual(summary['mean_rpc_ms'],2)

    def test_no_success_produces_missing_latency(self):
        records=[dict(ok=False,started=1,rpc_ms=None,end_to_end_ms=3,start_lag_ms=0)]
        self.assertIsNone(summarize(records,1,'cached',10,1)['p99_rpc_ms'])

    def test_scheduler_bounds_backlog_and_records_drops(self):
        def slow_request(*args):
            time.sleep(.03)
            return dict(ok=True,started=time.perf_counter(),error='')
        with patch('wordcount.benchmark.request',side_effect=slow_request):
            records,_=run_load('unused',1,[('alice','rabbit',2)]*20,1000,1,1)
        self.assertEqual(len(records),20)
        self.assertTrue(any(row['error']=='client_capacity_drop' for row in records))
        self.assertTrue(any(row['ok'] for row in records))


if __name__=='__main__':
    unittest.main()
