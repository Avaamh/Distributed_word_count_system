# Phase 3: estimated least response time

This increment adds a second dynamic policy and optional worker telemetry. It does not complete the performance comparison or add Phase 4 failure exclusion.

## Algorithm

Least connections remains available as `least_connections`. The second policy is `estimated_response_time`.

Workers record count-method duration, including optional artificial delay and response construction/logging, in a thread-safe exponentially weighted moving average (EWMA). For a new measurement `x`, the estimate is `0.2*x + 0.8*previous`; the first sample initializes the estimate. All count invocations are timed when metrics are enabled, including invalid requests and exceptions. Pings, popularity reads, and metrics reads do not update this count-duration estimate.

Each worker exposes `/metrics` on its internal HTTP port 18863. The balancer polls the three endpoints concurrently, waits up to 750 ms per poll, then sleeps for one second. It validates worker identity, counters, and timing values before using them. Both balancing policies poll telemetry in the Phase 3 deployment, keeping monitoring overhead comparable.

The selection score is:

```text
score_ms = estimated_query_duration_ms * (active_proxied_connections + 1)
```

The smallest score wins, with rotating ties. This heuristic penalizes outstanding connections and recent slow processing. It is not an exact queueing model: a connection is not necessarily an active count call, workers are threaded, and queries vary in size and cache status. The estimate excludes network delay, RPC method lookup, and time before execution reaches the count handler.

Worker-reported active query counts are exposed for diagnostics but are not an additional score factor. The score uses the balancer's local connection count.

For initial sampling, idle workers with no reported completed queries get a turn. Once timing samples exist, every tenth selection explores the worker least recently selected, even if it previously appeared slow. This provides fresh measurements and permits recovery from old slow estimates; it can deliberately route a query to a slower or busy worker.

Telemetry is treated as old if its fetch age exceeds three polling intervals or the most recent query sample is older than 15 seconds. Old/unknown estimates fall back to the largest fresh peer estimate with a floor of 1 ms; fresh estimates have a 0.001 ms numerical floor. Old telemetry does not mean instantaneous processing and does not automatically exclude a worker.

This is an application-duration-based estimated-response-time policy. It is not a byte-for-byte reproduction of a production proxy's least-time implementation, nor a direct measurement of end-to-end client response time.

## Architecture boundary

The client and workers still communicate through RPyC over the TCP byte proxy. The balancer neither imports RPyC nor decodes application requests. HTTP is used only for internal telemetry, separate from the forwarded data stream.

`worker_health_monitoring` remains false in proxy status. A failed metrics poll increments `metrics_errors`, but the worker stays eligible. Explicit health state, failure exclusion, and re-admission remain Phase 4 work.

## Worker configuration

The optional `METRICS_PORT` enables query measurement and starts an internal threaded HTTP endpoint after document startup validation. It is enabled in `compose.phase3.yaml` and absent in the original `compose.yaml`. The original Phase 2 configuration therefore leaves timing telemetry disabled. Updated source still has a different implementation hash, and any comparison to the previously recorded baseline should acknowledge the extra telemetry overhead in Phase 3.

The optional `QUERY_DELAY_MS` adds a sleep before each count method for a controlled response-time experiment. This applies to cache hits too, and its delay is included in the worker EWMA and client RPC timing. The existing response `processing_ms` starts inside the original count handler and excludes this artificial delay. This experiment emulates delayed service responses, not a slower CPU or a real outage.

Compose variables:

- `LB_ALGORITHM`: `least_connections` or `estimated_response_time`, defaulting to least connections.
- `WORKER1_DELAY_MS`: injected delay for worker 1, default 0. Other workers retain 0.

Ports are internal to the Compose network. This local-lab HTTP endpoint is not intended as a publicly exposed service.

## Files

Replace `wordcount/server.py`, `wordcount/load_balancer.py`, `wordcount/check_distribution.py`, and `compose.phase3.yaml`.

Add `wordcount/worker_metrics.py`, `tests/test_response_time.py`, and this document. Keep all other files and prior evidence.

## Start the second algorithm

From the repository root, with Docker Desktop running:

```bash
export LB_ALGORITHM=estimated_response_time
export WORKER1_DELAY_MS=0
docker compose -f compose.phase3.yaml config
docker compose -f compose.phase3.yaml up --build -d --wait redis server1 server2 server3 balancer
```

Run the tests:

```bash
docker run --rm --mount "type=bind,source=$PWD,target=/app,readonly" -w /app wordcount-lab:phase3 python -B -m unittest discover -s tests -v
```

Expected: 52 passing tests. Development checks also exercised three real RPyC worker endpoints, the HTTP metrics endpoints, and the proxy together. Compose runtime verification is still required on the laptop.

Send queries and inspect status:

```bash
docker compose -f compose.phase3.yaml run --rm distribution --requests 15 --interval 0.1
docker compose -f compose.phase3.yaml exec balancer python -m wordcount.balancer_status --host 127.0.0.1
```

The `--interval` option waits after each response; this is a sequential routing demonstration, not the open-loop performance benchmark. The status should report `algorithm: estimated_response_time`, `metrics_polling: true`, and nonzero completed-query counts after a poll. Equal distribution is not required: small timing differences and exploration affect routing.

## Controlled slow-worker demonstration

Create an evidence directory:

```bash
mkdir -p docs/evidence/phase3
```

First select least connections and add 80 ms of delay to worker 1:

```bash
export LB_ALGORITHM=least_connections
export WORKER1_DELAY_MS=80
docker compose -f compose.phase3.yaml up -d --wait server1 balancer
docker compose -f compose.phase3.yaml run --rm distribution --requests 60 --interval 0.1 --summary-only | tee docs/evidence/phase3/least_connections_slow_worker.txt
```

The sequential connections tend to tie at zero active connections, so the rotating tie-breaker should distribute them approximately evenly. Exact totals are observations, not fixed assertions.

Switch the proxy policy while retaining the same delay:

```bash
export LB_ALGORITHM=estimated_response_time
docker compose -f compose.phase3.yaml up -d --wait balancer
docker compose -f compose.phase3.yaml run --rm distribution --requests 60 --interval 0.1 --summary-only | tee docs/evidence/phase3/estimated_response_time_slow_worker.txt
docker compose -f compose.phase3.yaml exec balancer python -m wordcount.balancer_status --host 127.0.0.1 > docs/evidence/phase3/estimated_response_time_status.json
```

Worker 1 should generally receive fewer queries under the response-time policy. It should still receive some queries because initial sampling and exploration are intentional. Inspect its EWMA alongside the faster workers. The exact distribution may vary; it is not a guarantee that worker 2 and worker 3 will split equally.

This small demonstration verifies routing adaptation under an explicitly injected delay. It does not establish which policy performs best under the full benchmark workload. The same workers and shared cache remain running while the policy switches; do not treat these totals as an independent, randomized performance experiment.

Reset the artificial delay before future benchmarks:

```bash
export WORKER1_DELAY_MS=0
export LB_ALGORITHM=least_connections
docker compose -f compose.phase3.yaml up -d --wait server1 balancer
```

## Commit and remaining work

After the laptop tests and demonstrations pass, commit the seven update files and the new evidence:

```text
feat(phase-3): add estimated-response-time balancing and worker telemetry
```

Phase 3 still needs the complete five-rate runs for both policies, correctly labeled comparison plots, comparison against the Phase 2 baseline, results interpretation, and README updates. The benchmark currently retains its Phase 2 plot title; do not run the formal Phase 3 experiment until the comparison workflow has been updated.

## Manual validation results

- Estimated response time with worker 1 slowed: worker totals were 5, 42, and 13.
- Least connections with worker 1 slowed and three concurrent clients: combined totals were 7, 26, and 27.
- These tests used different workloads and do not establish which algorithm is faster.
- With worker health monitoring disabled, stopping worker 1 caused a client-visible EOFError. The distribution test exited early.
- After restarting worker 1, all 60 requests completed successfully, with 20 handled by each worker.
- Automatic failover was not demonstrated by these tests.
