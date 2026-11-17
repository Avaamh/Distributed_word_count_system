"""Exercise cache decisions and Redis command boundaries without a daemon."""

import json
import unittest
from unittest.mock import Mock, patch

import redis

from wordcount.cache import ResultCache
from wordcount.server import WordCountService


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = ResultCache()
        self.cache.client = Mock()

    def test_miss_is_distinct_from_cached_zero(self):
        self.cache.client.get.return_value = None
        self.assertIsNone(self.cache.get("hash", "rabbit"))
        self.cache.client.get.return_value = "0"
        self.assertEqual(self.cache.get("hash", "rabbit"), 0)

    def test_invalid_cache_value_is_treated_as_miss(self):
        for value in ("bad", "-1", "2.5"):
            self.cache.client.get.return_value = value
            self.assertIsNone(self.cache.get("hash", "rabbit"))

    def test_document_versions_have_distinct_keys(self):
        self.assertNotEqual(self.cache.key("old", "rabbit"), self.cache.key("new", "rabbit"))

    def test_cache_entry_has_expiration(self):
        self.cache.put("hash", "rabbit", 47)
        self.cache.client.set.assert_called_once_with(
            self.cache.key("hash", "rabbit"), 47, ex=3600)

    def test_top_keywords_are_serializable(self):
        self.cache.client.zrevrange.return_value = [("rabbit", 3.0)]
        self.assertEqual(self.cache.top_keywords(1), [{"keyword": "rabbit", "requests": 3}])


class CachedServiceTests(unittest.TestCase):
    def setUp(self):
        self.cache = Mock()
        self.cache.get.return_value = None
        self.patches = [
            patch("wordcount.server.CACHE", self.cache),
            patch("wordcount.server.DOCUMENT_HASHES", {"alice": "hash"}),
            patch("wordcount.server.count_document", return_value=47),
        ]
        for p in self.patches:
            result = p.start()
            self.addCleanup(p.stop)
        self.count = result
        self.service = WordCountService()

    def query(self, keyword="rabbit"):
        return json.loads(self.service.exposed_count("alice", keyword, "test"))

    def test_miss_computes_and_caches_result(self):
        response = self.query("RABBIT")
        self.assertEqual(response["cache_status"], "miss")
        self.assertEqual(response["count"], 47)
        self.count.assert_called_once_with("alice", "rabbit")
        self.cache.put.assert_called_once_with("hash", "rabbit", 47)
        self.cache.record_keyword.assert_called_once_with("rabbit")

    def test_hit_skips_counting_including_zero(self):
        self.cache.get.return_value = 0
        response = self.query()
        self.assertEqual(response["count"], 0)
        self.assertEqual(response["cache_status"], "hit")
        self.count.assert_not_called()
        self.cache.put.assert_not_called()
        self.cache.record_keyword.assert_called_once_with("rabbit")

    def test_read_failure_returns_uncached_count(self):
        self.cache.get.side_effect = redis.ConnectionError("offline")
        response = self.query()
        self.assertEqual(response["count"], 47)
        self.assertEqual(response["cache_status"], "bypass")
        self.assertFalse(response["statistics_recorded"])
        self.cache.put.assert_not_called()

    def test_write_failure_preserves_count(self):
        self.cache.put.side_effect = redis.TimeoutError("timeout")
        response = self.query()
        self.assertTrue(response["ok"])
        self.assertEqual(response["count"], 47)
        self.assertEqual(response["cache_status"], "bypass")

    def test_statistics_failure_preserves_cache_hit(self):
        self.cache.get.return_value = 47
        self.cache.record_keyword.side_effect = redis.ConnectionError("offline")
        response = self.query()
        self.assertTrue(response["ok"])
        self.assertEqual(response["cache_status"], "hit")
        self.assertFalse(response["statistics_recorded"])

    def test_invalid_keyword_is_not_recorded(self):
        self.assertFalse(self.query("two words")["ok"])
        self.cache.get.assert_not_called()
        self.cache.record_keyword.assert_not_called()

    def test_top_endpoint_checks_limit_and_availability(self):
        self.assertEqual(json.loads(self.service.exposed_top_keywords(0))["error"], "invalid_limit")
        self.cache.top_keywords.side_effect = redis.ConnectionError("offline")
        self.assertEqual(json.loads(self.service.exposed_top_keywords())["error"], "statistics_unavailable")


if __name__ == "__main__":
    unittest.main()
