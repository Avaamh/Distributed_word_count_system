"""Phase 2 RPyC worker; Redis will be added in the next increment."""

import json
import logging
import os
import time

import rpyc
from rpyc.utils.server import ThreadedServer

from wordcount.counting import DOCUMENTS, count_document, load_document, normalize_keyword

LOGGER = logging.getLogger("wordcount.worker")
SERVER_ID = os.environ.get("SERVER_ID", "worker-1")
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
            count = count_document(document_id, keyword)
            response.update(
                ok=True, document_id=document_id, keyword=keyword,
                count=count, cache_status="disabled",
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


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for document_id in DOCUMENTS:
        load_document(document_id)
    LOGGER.info("Starting %s; verified %s documents; cache disabled", SERVER_ID, len(DOCUMENTS))
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
