# Phase 2: Redis caching increment

## Behavior

The worker validates a document ID and keyword, then checks Redis for the count. A cache miss triggers counting and stores the result for 3600 seconds. A hit returns the stored count without opening or scanning the document. Cached zero counts are valid hits.

At startup, the worker loads each registered document and hashes its extracted text. Keys contain `english-v1`, the SHA-256 content hash, and the normalized keyword. Files must remain unchanged while the worker is running. After editing any book, recreate the worker so hashes and content agree. This is the fixed-document collection policy described in the architecture design.

Cache keys have the form `wc:result:english-v1:<content-hash>:rabbit`.

The `wc:keyword_popularity` sorted set tracks normalized keyword query counts across documents. Successful count invocations, including cache hits, increment popularity by one when Redis is available. Invalid queries and health checks do not increment it. The `top` query is observational and does not increment it. Statistics are best effort, not exactly once; a lost response or retry can make them differ from completed client requests. Ties follow Redis's reverse lexical ordering.

## Files

New: `wordcount/cache.py`, `tests/test_cache.py`, this document, and `data/SOURCES.md`.

Updated: `wordcount/server.py`, `wordcount/client.py`, `requirements.txt`, and `compose.yaml`.

The previous Dockerfile, counting module, counting tests, and RPC tests remain compatible. Redis runs in its own container without a published host port. The worker also has no published host port; the client reaches it on the Compose network.

## Start and verify

Run these commands from the repository root:

```bash
docker compose config
docker compose up --build -d --wait redis server
docker compose ps
docker compose run --rm client query alice rabbit
docker compose run --rm client query alice rabbit
docker compose run --rm client top --limit 5
```

With a fresh Redis process and unchanged books, the first query should return count 47 with `cache_status: miss`, and the second count 47 with `cache_status: hit`. The popularity result should show two requests for `rabbit` if these were the only count queries. Actual counts are edition-dependent. A repeated demonstration can start with a cache hit if that key already exists.

Inspecting Redis is optional:

```bash
docker compose exec redis redis-cli --scan --pattern 'wc:result:*'
docker compose exec redis redis-cli ZREVRANGE wc:keyword_popularity 0 4 WITHSCORES
```

## Tests

After rebuilding:

```bash
docker run --rm --mount "type=bind,source=$PWD,target=/app,readonly" -w /app wordcount-lab:phase2 python -B -m unittest discover -s tests -v
```

Expected: 29 tests, all passing. The automated suite uses mock Redis command boundaries and genuine TCP RPC connections with temporary documents. Check actual Redis interoperability using the miss/hit and top commands above. Development verification also exercised these operations against a local Redis process; Docker Compose must still be verified on the target laptop.

## Availability and durability limits

Redis connection and socket read timeouts are 200 ms each; automatic command retries are disabled. These are individual socket-operation limits, not a strict 200 ms overall request deadline. DNS resolution and connection setup can add time. On a Redis read/write failure, the worker returns the directly computed count with `cache_status: bypass`; if a popularity update fails after a hit, the hit remains valid and `statistics_recorded` is false. It tries Redis again on subsequent requests. Phase 4 will add a cooldown/circuit breaker to reduce repeated attempts during outages.

The server health check is independent of Redis. A Redis failure should not make the counting worker unhealthy. Redis has no persistence in this local experiment configuration: stopping/restarting its process loses cache entries and popularity totals. New entries and statistics build up again afterward. Replication and Sentinel are outside our agreed scope.

No cache lock prevents simultaneous misses from computing the same result. This can add duplicate work under concurrency, but does not change the count for an immutable document.

## Milestone status

Redis caching and popularity queries are implemented. Phase 2 still requires target-laptop verification, the five-rate benchmark, mean and p99 plots, and experiment documentation before the `phase-2` tag is created.
