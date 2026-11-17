"""CLI for a single health check or count request; no automatic retries yet."""

import argparse
import json
import socket
import sys
import time
import uuid

import rpyc
from rpyc.core.stream import SocketStream


def connect(host, port):
    sock = socket.create_connection((host, port), timeout=3)
    # RPyC manages reads; its synchronous RPC timeout is configured separately.
    sock.settimeout(None)
    try:
        return rpyc.connect_stream(
            SocketStream(sock), config={"sync_request_timeout": 5, "allow_pickle": False},
        )
    except Exception:
        sock.close()
        raise


def main():
    parser = argparse.ArgumentParser(description="Query the word-count worker.")
    parser.add_argument("--host", default="server")
    parser.add_argument("--port", type=int, default=18861)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health", help="Check the worker RPC endpoint.")
    query = commands.add_parser("query", help="Count one keyword in a registered book.")
    query.add_argument("document")
    query.add_argument("keyword")
    args = parser.parse_args()
    try:
        start = time.perf_counter()
        with connect(args.host, args.port) as conn:
            connection_ms = (time.perf_counter() - start) * 1000
            if args.command == "health":
                print(f"healthy: {conn.root.ping()}")
                return 0
            request_id = str(uuid.uuid4())
            # Resolve the remote method before starting the RPC timer.
            remote_count = conn.root.count
            start = time.perf_counter()
            raw_response = remote_count(args.document, args.keyword, request_id)
            rpc_ms = (time.perf_counter() - start) * 1000
            response = json.loads(raw_response)
            response["connection_ms"] = round(connection_ms, 3)
            response["rpc_ms"] = round(rpc_ms, 3)
            print(json.dumps(response, indent=2))
            return 0 if response["ok"] else 1
    except (OSError, EOFError, TimeoutError, ValueError) as exc:
        print(f"Client error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
