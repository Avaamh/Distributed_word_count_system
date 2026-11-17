"""Exercise real RPC connections against temporary, known document contents."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from rpyc.utils.server import ThreadedServer

from wordcount.client import connect
from wordcount.counting import count_text, load_document
from wordcount.server import PROTOCOL_CONFIG, WordCountService


class RpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        folder = Path(cls.directory.name)
        (folder / "alice_in_wonderland.txt").write_text("Rabbit rabbit! Rabbits.", encoding="utf-8")
        cls.counter = patch(
            "wordcount.server.count_document",
            side_effect=lambda doc, key: count_text(load_document(doc, folder), key),
        )
        cls.counter.start()
        cls.server = ThreadedServer(WordCountService, hostname="127.0.0.1", port=0,
                                    protocol_config=PROTOCOL_CONFIG, auto_register=False)
        cls.thread = threading.Thread(target=cls.server.start, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.close()
        cls.thread.join(timeout=3)
        cls.counter.stop()
        cls.directory.cleanup()

    def query(self, doc="alice", keyword="RABBIT", request_id="test-1"):
        with connect("127.0.0.1", self.server.port) as conn:
            return json.loads(conn.root.count(doc, keyword, request_id))

    def test_count_over_rpc(self):
        result = self.query()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["keyword"], "rabbit")
        self.assertEqual(result["request_id"], "test-1")
        self.assertEqual(result["cache_status"], "disabled")

    def test_invalid_query_returns_error(self):
        self.assertEqual(self.query(keyword="two words")["error"], "invalid_query")

    def test_path_traversal_returns_error(self):
        self.assertEqual(self.query(doc="../README.md")["error"], "invalid_query")

    def test_missing_document_returns_error(self):
        with self.assertLogs("wordcount.worker", level="ERROR"):
            result = self.query(doc="sherlock")
        self.assertEqual(result["error"], "document_unavailable")

    def test_ping_over_rpc(self):
        with connect("127.0.0.1", self.server.port) as conn:
            self.assertTrue(conn.root.ping())


if __name__ == "__main__":
    unittest.main()
