"""Open-loop Phase 2 benchmark; run inside the Compose network."""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import platform
import random
import statistics
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from wordcount.cache import ResultCache
from wordcount.client import connect
from wordcount.counting import DOCUMENTS, RULE_VERSION, TOKEN, load_document


def percentile(values, percent):
    """Nearest-rank percentile. Return None when no samples succeeded."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)]


def request(host, port, document, keyword, expected, scheduled):
    started = time.perf_counter()
    row = dict(document=document, keyword=keyword, expected_count=expected,
               request_id=str(uuid.uuid4()), started=started, finished=started,
               start_lag_ms=max(0, (started - scheduled) * 1000),
               connection_ms=None, rpc_ms=None, end_to_end_ms=None,
               server_id="", cache_status="", ok=False, error="")
    try:
        with connect(host, port) as conn:
            row["connection_ms"] = (time.perf_counter() - started) * 1000
            remote_count = conn.root.count
            rpc_start = time.perf_counter()
            raw = remote_count(document, keyword, row["request_id"])
            received = time.perf_counter()
            row["rpc_ms"] = (received - rpc_start) * 1000
            response = json.loads(raw)
            row["end_to_end_ms"] = (received - started) * 1000
            row["cache_status"] = response.get("cache_status", "")
            row["server_id"] = response.get("server_id", "")
            row["ok"] = bool(response.get("ok")) and response.get("count") == expected
            if not row["ok"]:
                row["error"] = response.get("error", "incorrect_count")
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["finished"] = time.perf_counter()
    if row["end_to_end_ms"] is None:
        row["end_to_end_ms"] = (row["finished"] - started) * 1000
    row["scheduled_to_finish_ms"] = (row["finished"] - scheduled) * 1000
    return row


def run_load(host, port, workload, rate, workers, max_pending):
    """Schedule arrivals independently of responses, bounding client backlog."""
    records, pending = [], set()
    start = time.perf_counter() + .1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, (document, keyword, expected) in enumerate(workload):
            scheduled = start + index / rate
            delay = scheduled - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            finished = {future for future in pending if future.done()}
            records.extend(future.result() for future in finished)
            pending -= finished
            if len(pending) >= max_pending:
                records.append(dict(document=document, keyword=keyword, expected_count=expected,
                    request_id=str(uuid.uuid4()), started=None, finished=time.perf_counter(),
                    start_lag_ms=None, connection_ms=None, rpc_ms=None, end_to_end_ms=None,
                    scheduled_to_finish_ms=None, server_id="", cache_status="", ok=False,
                    error="client_capacity_drop"))
            else:
                pending.add(pool.submit(request, host, port, document, keyword, expected, scheduled))
        records.extend(future.result() for future in pending)
    elapsed = max(len(workload) / rate, time.perf_counter() - start)
    return records, elapsed


def summarize(records, elapsed, mode, rate, repeat):
    good = [row for row in records if row["ok"]]
    rpc = [row["rpc_ms"] for row in good]
    e2e = [row["end_to_end_ms"] for row in good]
    lag = [row["start_lag_ms"] for row in records if row["start_lag_ms"] is not None]
    expected_status = "hit" if mode == "cached" else "miss"
    return dict(mode=mode, rate_rps=rate, repeat=repeat, offered=len(records),
        sent=sum(row["started"] is not None for row in records), successful=len(good),
        failed=len(records)-len(good),
        failure_percent=100 * (len(records)-len(good)) / len(records),
        elapsed_s=elapsed, achieved_rps=len(good)/elapsed,
        mean_rpc_ms=statistics.mean(rpc) if rpc else None,
        p99_rpc_ms=percentile(rpc, 99),
        mean_end_to_end_ms=statistics.mean(e2e) if e2e else None,
        p99_end_to_end_ms=percentile(e2e, 99),
        p99_start_lag_ms=percentile(lag, 99),
        cache_condition_mismatches=sum(row["cache_status"] != expected_status for row in good),
        worker_counts=json.dumps(dict(Counter(row["server_id"] for row in good))))


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_plots(summaries, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    metrics = [("mean_rpc_ms", "Mean RPC latency (ms)", "mean_latency.png"),
               ("p99_rpc_ms", "p99 RPC latency (ms)", "p99_latency.png"),
               ("failure_percent", "Failed or dropped requests (%)", "failure_rate.png")]
    for metric, label, filename in metrics:
        fig, ax = plt.subplots(figsize=(7.5, 4.5))
        for mode in sorted({row["mode"] for row in summaries}):
            rates = sorted({row["rate_rps"] for row in summaries if row["mode"] == mode})
            means, lows, highs = [], [], []
            for rate in rates:
                values = [row[metric] for row in summaries
                          if row["mode"] == mode and row["rate_rps"] == rate and row[metric] is not None]
                mean = statistics.mean(values) if values else float("nan")
                means.append(mean)
                lows.append(mean-min(values) if values else 0)
                highs.append(max(values)-mean if values else 0)
            ax.errorbar(rates, means, yerr=[lows, highs], marker="o", capsize=4, label=mode)
        ax.set(xlabel="Offered request rate (requests/s)", ylabel=label,
               title="Single-worker Phase 2 benchmark")
        ax.set_xticks(sorted({row["rate_rps"] for row in summaries}))
        ax.grid(alpha=.25)
        ax.legend()
        fig.text(.02, .015, "Points: mean across repeats; bars: minimum–maximum. Latency uses successful requests only.", fontsize=8)
        fig.tight_layout(rect=(0,.05,1,1))
        fig.savefig(output/filename, dpi=160)
        plt.close(fig)


def prepare_books():
    hashes, counts, inventory = {}, {}, {}
    for document in DOCUMENTS:
        text = load_document(document)
        hashes[document] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        counts[document] = Counter(match.group().lower() for match in TOKEN.finditer(text.replace("\u2019", "'")))
        inventory[document] = dict(filename=DOCUMENTS[document], content_sha256=hashes[document],
                                   characters=len(text), tokens=sum(counts[document].values()))
    return hashes, counts, inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="server")
    parser.add_argument("--port", type=int, default=18861)
    parser.add_argument("--redis-host", default="redis")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--rates", nargs="+", type=int, default=[10,20,30,40,50])
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--mode", choices=["cached","uncached","both"], default="both")
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--max-pending", type=int, default=128)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--label", default="phase2")
    args = parser.parse_args()
    if min(args.rates + [args.duration,args.repeats,args.workers,args.max_pending]) < 1:
        parser.error("Rates, duration, repeats, workers, and max-pending must be positive.")
    if args.max_pending < args.workers:
        parser.error("max-pending must be at least workers.")
    if not all(c.isalnum() or c in "-_" for c in args.label):
        parser.error("Label may contain letters, digits, hyphens, and underscores.")
    cache = ResultCache(host=args.redis_host, port=args.redis_port)
    try:
        cache.client.ping()
        hashes, counts, inventory = prepare_books()
        warm = [(doc, word, counts[doc][word]) for doc in DOCUMENTS for word in ("the","and","of","to")]
        # Seeded unique pairs make each uncached query a distinct result key.
        rng = random.Random(args.seed)
        pools = []
        for doc in DOCUMENTS:
            entries = [(doc,word,count) for word,count in sorted(counts[doc].items())]
            rng.shuffle(entries)
            pools.append(entries)
        cold = [entry for group in zip(*pools) for entry in group]
        if len(cold) < max(args.rates)*args.duration and args.mode != "cached":
            parser.error("Not enough distinct document/keyword pairs; reduce duration or rate.")
        check = request(args.host,args.port,*warm[0],time.perf_counter())
        if not check["ok"] or check["cache_status"] not in ("hit","miss"):
            raise RuntimeError(f"Worker/Redis preflight failed: {check}")
        for doc,word,expected in warm:
            row=request(args.host,args.port,doc,word,expected,time.perf_counter())
            if not row["ok"] or row["cache_status"] not in ("hit","miss"):
                raise RuntimeError(f"Warm-up failed: {row}")
        row=request(args.host,args.port,*warm[0],time.perf_counter())
        if not row["ok"] or row["cache_status"] != "hit":
            raise RuntimeError("Redis preflight failed: repeated query did not produce a hit.")
    except Exception as exc:
        parser.exit(1,f"Preflight error: {exc}\n")
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output=Path("results")/f"{args.label}_{stamp}_{uuid.uuid4().hex[:6]}"
    output.mkdir(parents=True,exist_ok=False)
    metadata=dict(settings=vars(args),timestamp_utc=stamp,rule_version=RULE_VERSION,
        python=platform.python_version(),container_platform=platform.platform(),
        packages={name:importlib.metadata.version(name) for name in ("rpyc","redis","matplotlib")},
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")},
        documents=inventory,latency_definition="Client count RPC invocation to receipt, excluding connection and method lookup",
        percentiles="Nearest rank per repeat; plots average repeat statistics, bars show min/max")
    (output/"metadata.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
    modes=["cached","uncached"] if args.mode=="both" else [args.mode]
    summaries=[]
    try:
        for mode in modes:
            for rate in args.rates:
                for repeat in range(1,args.repeats+1):
                    size=rate*args.duration
                    workload=[warm[i%len(warm)] for i in range(size)] if mode=="cached" else cold[:size]
                    if mode=="uncached":
                        # Delete only exact keys in this run; never flush Redis or popularity.
                        keys=[cache.key(hashes[doc],word) for doc,word,_ in workload]
                        for i in range(0,len(keys),100):cache.client.delete(*keys[i:i+100])
                    print(f"Running {mode}: {rate} req/s, repeat {repeat}/{args.repeats}",flush=True)
                    records,elapsed=run_load(args.host,args.port,workload,rate,args.workers,args.max_pending)
                    for row in records:row.update(mode=mode,rate_rps=rate,repeat=repeat)
                    write_csv(output/f"requests_{mode}_{rate}rps_r{repeat}.csv",records)
                    summary=summarize(records,elapsed,mode,rate,repeat)
                    summaries.append(summary)
                    write_csv(output/"summary.csv",summaries)
                    print(f"  successes={summary['successful']}/{size}, throughput={summary['achieved_rps']:.2f} req/s, p99={summary['p99_rpc_ms']} ms, cache mismatches={summary['cache_condition_mismatches']}",flush=True)
        make_plots(summaries,output)
        print(f"Saved measurements and plots to {output}",flush=True)
        if any(row['failed'] or row['cache_condition_mismatches'] for row in summaries):
            print("Some runs had failures or unexpected cache status; inspect summary.csv before drawing conclusions.",flush=True)
    except Exception as exc:
        parser.exit(1,f"Benchmark error: {exc}. Completed measurements remain in {output}\n")
    finally:
        cache.client.close()


if __name__ == "__main__":
    main()
