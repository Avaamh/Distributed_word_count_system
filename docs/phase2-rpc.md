# Phase 2: RPC increment

Implemented at this milestone: registered-document counting through a custom RPyC service, CLI client, application-level ping, request IDs, structured responses, and basic timing. Redis, retries, replication, and balancing are planned for later increments.

## Files and prerequisites

Retain `wordcount/counting.py` and the counting tests from the previous increment. Keep the original downloaded texts under `data/alice_in_wonderland.txt`, `data/sherlock_holmes.txt`, and `data/war_and_peace.txt`. Docker Desktop must be running. No host Python installation is needed.

`requirements.txt` pins RPyC and its dependency. The Dockerfile installs them and runs Python as a non-root user. `compose.yaml` defines the server and an on-demand client on the Compose network. The server port is not published to the host. The data directory is mounted read-only, not embedded in the image.

## Run

From the repository root:

```bash
docker compose config
docker compose up --build -d --wait server
docker compose ps
docker compose run --rm client query alice rabbit
```

The server checks all registered files at startup. Compose uses the client's `health` command to check the RPC endpoint. This initial liveness check is not the final continuous document-readiness check planned for Phase 4.

Successful query responses contain `ok`, `document_id`, `keyword`, `count`, `request_id`, `server_id`, `cache_status`, and `processing_ms`. The client adds `connection_ms` and `rpc_ms`. Cache status is `disabled` because this increment has no Redis integration.

`processing_ms` covers the server's validation, document reading, and counting. `rpc_ms` measures the count invocation from the client, excluding initial connection setup and remote-method lookup. `connection_ms` measures connection creation. These are diagnostic single-query timings, not the required benchmark experiments.

## Tests

After building the image:

```bash
docker run --rm --mount "type=bind,source=$PWD,target=/app,readonly" -w /app wordcount-lab:phase2 python -B -m unittest discover -s tests -v
```

There are 12 counting tests and 5 RPC tests. RPC tests use real TCP connections with small temporary documents independent of the downloaded books.

## Validation and logs

```bash
docker compose run --rm client query alice "two words"
docker compose run --rm client query ../README.md rabbit
docker compose logs --tail=20 server
```

The first two commands should return structured errors and a nonzero exit status. Worker logs include request identifiers and results. RPyC may also emit connection logs.

To stop this project's containers and remove its Compose network:

```bash
docker compose down
```

Downloaded documents and repository files remain on the laptop.

## Limits

One server, no Redis, no load balancer, and no automatic retries. The client has connection and RPC timeouts but does not yet implement the final overall logical-query deadline. A threaded server supports simultaneous connections; CPU scaling is not established by this increment. The local deployment has no application authentication or transport encryption.
