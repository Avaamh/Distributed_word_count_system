# Phase 2 benchmark procedure

Status: benchmark implementation. Performance results must come from the target laptop. Development smoke tests verify the pipeline, not the laptop's performance.

## Setup

Keep the counting, RPC, and Redis increments installed. Add `wordcount/benchmark.py`, `wordcount/plot_results.py`, and `tests/test_benchmark.py`. Replace `requirements.txt` and `compose.yaml`. No existing book or implementation module is replaced.

From the repository root on macOS:

```bash
mkdir -p results
export BENCHMARK_UID="$(id -u)"
export BENCHMARK_GID="$(id -g)"
docker compose config
docker compose up --build -d --wait redis server
```

The benchmark container uses the laptop user's numeric IDs so its bind-mounted results directory is writable. Source code is baked into the image; rebuild after changing code. Books are mounted read-only. Results are written to a unique timestamped directory, never over existing runs.

## Automated tests and smoke run

```bash
docker run --rm --mount "type=bind,source=$PWD,target=/app,readonly" -w /app wordcount-lab:phase2 python -B -m unittest discover -s tests -v
docker compose run --rm benchmark --rates 10 20 --duration 3 --repeats 1 --label phase2_smoke
```

Expect 33 passing tests. The smoke run tests two rates and has too few samples for useful tail-latency conclusions. The benchmark preflight requires reachable Redis, correct worker answers, and a cache hit on a repeated query. On failure it exits before the experiment. It also compares returned counts to reference counts computed from the local book copies.

## Full experiment

Close unnecessary applications, keep Docker's resource allocation unchanged, connect the laptop to power, and prevent sleep during the run. Do not issue other queries or modify the books during an experiment.

```bash
docker compose run --rm benchmark --rates 10 20 30 40 50 --duration 30 --repeats 3 --label phase2_full
```

This offers traffic for approximately 15 minutes: 5 rates x 30 seconds x 3 repeats x 2 modes. Setup and waiting for outstanding calls can add time. Every rate is at least 10 requests/second, and adjacent rates differ by 10. At 10 requests/second there are 300 requests per repeat; p99 estimates at this sample size have limited precision. Increase duration to 60 seconds if more samples are needed, preserving settings across later comparisons.

Record host details, as the benchmark metadata describes the Linux container rather than macOS hardware:

```bash
sw_vers > results/macos-version.txt
sysctl -n machdep.cpu.brand_string > results/cpu-model.txt
docker info --format 'CPUs={{.NCPU}} MemoryBytes={{.MemTotal}} Architecture={{.Architecture}}' > results/docker-resources.txt
docker --version > results/docker-version.txt
docker compose version > results/compose-version.txt
git rev-parse HEAD > results/git-revision.txt
git status --short > results/git-working-tree.txt
```

Keep these settings with the chosen full run. A recorded revision may precede uncommitted edits; the working-tree status and benchmark source hashes document this distinction.

## Workloads

- **Cached:** cycle evenly through 12 pairs: the words `the`, `and`, `of`, and `to` in each of the three books. Preflight warms these pairs before timed runs. Expected status is `hit`.
- **Uncached:** generate seeded, shuffled lists of distinct actual words for each book, then interleave the books. Each document/keyword pair occurs at most once per run. Before each run delete only its exact result keys, preserving all other keys and popularity statistics. Expected status is `miss`. Results are still written to Redis as in normal service operation.

The two modes differ in keyword selection; they are contrasting cache workloads, not an isolated estimate of caching speedup on an identical keyword distribution. Both use approximately equal document shares. The seed and hashes make runs reproducible. Use the same workload settings and document versions in Phase 3 comparisons.

The uncached workload is limited to three times the smallest book's distinct-token vocabulary. The benchmark rejects workloads that need more unique pairs. Each repeat reuses the same selected workload so run-to-run variability is not mixed with a changed query list.

## Scheduling and latency definitions

One count query uses one fresh TCP/RPyC connection. A thread pool supports concurrent outstanding queries. An open-loop scheduler offers arrivals at fixed intervals independent of responses. At most 128 tasks can be running or queued by default; excess arrivals are recorded as `client_capacity_drop`. The scheduler does not silently reduce the intended number of arrivals.

Per request:

- `rpc_ms`: client invocation of the resolved remote count method to receipt of the returned value, excluding initial connection setup and remote-method lookup. This is the primary assignment latency metric.
- `connection_ms`: connection creation time.
- `end_to_end_ms`: worker-thread start to receipt, including connection setup and method lookup. It excludes executor queue waiting.
- `start_lag_ms`: scheduled arrival to actual worker-thread start, capturing generator/queue delay.
- `scheduled_to_finish_ms`: scheduled arrival to task completion, including delay, connection setup, response handling, and cleanup.

No automatic client retries occur. Timeouts and errors are retained. The socket and RPC timeouts from the CLI implementation apply; a strict overall logical-query deadline remains a later feature.

`achieved_rps` is successful queries divided by the observation duration, including any drain time beyond the offered window. Compare this with offered rate. A growing dispatch lag, many drops, or throughput below offered rate means the system/generator could not sustain the workload; do not interpret a low successful-request latency alone as good performance.

## Output

Each run directory contains:

| File | Content |
|---|---|
| `requests_<mode>_<rate>rps_r<repeat>.csv` | Every offered query, including failures and client-capacity drops |
| `summary.csv` | Per-repeat latency, throughput, errors, cache-condition mismatches, and worker distribution |
| `metadata.json` | Settings, package versions, source hashes, document hashes and sizes |
| `mean_latency.png` | Mean count-RPC latency against offered rate |
| `p99_latency.png` | p99 count-RPC latency against offered rate |
| `failure_rate.png` | Failed or dropped fraction against offered rate |

Latency statistics use correct successful requests only, with errors separately reported. Percentiles use the nearest-rank definition. Each plotted point averages the per-repeat statistic; bars show the smallest and largest repeat statistic, not confidence intervals. The plotted p99 is the mean of per-repeat p99s, not a pooled p99. Runs with no successes have no latency point.

Inspect failure rates and `cache_condition_mismatches` before interpreting the curves. A cache mismatch means the intended cache workload was not achieved; such a run needs investigation. Benchmark errors preserve completed CSVs. Popularity totals include preflight and benchmark queries.

To regenerate plots without rerunning traffic:

```bash
docker compose run --rm --entrypoint python benchmark -m wordcount.plot_results results/NAME_OF_YOUR_RUN_DIRECTORY
```

Replace `NAME_OF_YOUR_RUN_DIRECTORY` with an existing run directory printed by the benchmark.

## Review before tagging

Verify the smoke test and full experiment on the laptop. Examine correctness failures, throughput, cache statuses, scheduling delay, and the three plots. Explain any saturation or error behavior. Choose the full run, retain its measurements and environment details, then write actual results and limitations into the README/report. Do not tag `phase-2` solely because the benchmark code exists.
