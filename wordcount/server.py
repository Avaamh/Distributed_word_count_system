"""Phase 2 RPyC worker with Redis result caching and popularity queries."""

import hashlib
import json
import logging
import os
import time

import redis
import rpyc
from rpyc.utils.server import ThreadedServer

from wordcount.cache import ResultCache
from wordcount.counting import DOCUMENTS, count_document, load_document, normalize_keyword

LOGGER = logging.getLogger("wordcount.worker")
SERVER_ID = os.environ.get("SERVER_ID", "worker-1")
CACHE = None
DOCUMENT_HASHES = {}
PROTOCOL_CONFIG = {
    "allow_pickle": False,
    "allow_public_attrs": False,
    "allow_all_attrs": False,
    "include_local_traceback": False,
}


class WordCountService(rpyc.Service):
    def exposed_ping(self):
        return SERVER_ID

    def exposed_count(self, document_id, keyword, request_id):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
            return json.dumps({"ok": False, "error": "invalid_request_id"})
        start = time.perf_counter()
        response = {"request_id": request_id, "server_id": SERVER_ID}
        try:
            keyword = normalize_keyword(keyword)
            if not isinstance(document_id, str) or document_id not in DOCUMENTS:
                raise ValueError("Unknown document ID. Choose alice, sherlock, or war_and_peace.")
            count = None
            cache_status = "disabled" if CACHE is None else "miss"
            statistics_recorded = False
            cache_available = CACHE is not None
            if cache_available:
                try:
                    count = CACHE.get(DOCUMENT_HASHES[document_id], keyword)
                    if count is not None:
                        cache_status = "hit"
                except redis.RedisError:
                    cache_available = False
                    cache_status = "bypass"
            if count is None:
                count = count_document(document_id, keyword)
                if cache_available:
                    try:
                        CACHE.put(DOCUMENT_HASHES[document_id], keyword, count)
                    except redis.RedisError:
                        cache_available = False
                        cache_status = "bypass"
            if cache_available:
                try:
                    CACHE.record_keyword(keyword)
                    statistics_recorded = True
                except redis.RedisError:
                    pass
            response.update(
                ok=True, document_id=document_id, keyword=keyword,
                count=count, cache_status=cache_status,
                statistics_recorded=statistics_recorded,
            )
        except ValueError as exc:
            response.update(ok=False, error="invalid_query", message=str(exc))
        except (OSError, UnicodeError):
            LOGGER.exception("Document unavailable request_id=%s", request_id)
            response.update(ok=False, error="document_unavailable",
                            message="The registered document could not be read.")
        response["processing_ms"] = round((time.perf_counter() - start) * 1000, 3)
        LOGGER.info(json.dumps(response))
        # A JSON string is passed by value, avoiding additional remote-object calls.
        return json.dumps(response)

    def exposed_top_keywords(self, limit=10):
        if type(limit) is not int or not 1 <= limit <= 100:
            return json.dumps({"ok": False, "error": "invalid_limit",
                               "message": "Limit must be an integer from 1 to 100."})
        if CACHE is None:
            return json.dumps({"ok": False, "error": "cache_disabled"})
        try:
            return json.dumps({"ok": True, "keywords": CACHE.top_keywords(limit),
                               "statistics": "best_effort"})
        except redis.RedisError:
            return json.dumps({"ok": False, "error": "statistics_unavailable"})


def main():
    global CACHE, DOCUMENT_HASHES
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for document_id in DOCUMENTS:
        text = load_document(document_id)
        DOCUMENT_HASHES[document_id] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    CACHE = ResultCache(host=os.environ.get("REDIS_HOST", "redis"),
                        port=int(os.environ.get("REDIS_PORT", "6379")))
    LOGGER.info("Starting %s; verified %s documents; Redis caching configured", SERVER_ID, len(DOCUMENTS))
    server = ThreadedServer(
        WordCountService, hostname="0.0.0.0", port=18861,
        protocol_config=PROTOCOL_CONFIG, auto_register=False,
    )
    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


if __name__ == "__main__":
    main()
