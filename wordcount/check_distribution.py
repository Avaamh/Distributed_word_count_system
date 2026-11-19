"""Check counts and display worker IDs through a TCP proxy."""
import argparse
from collections import Counter
from wordcount.benchmark import request
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host",default="balancer")
    parser.add_argument("--port",type=int,default=18860)
    parser.add_argument("--requests",type=int,default=9)
    parser.add_argument("--interval",type=float,default=0)
    parser.add_argument("--summary-only",action="store_true")
    args=parser.parse_args()
    if args.interval<0:parser.error("interval must not be negative")
    if args.requests<1:parser.error("requests must be positive")
    # Compute a local reference count from the same registered book edition.
    from wordcount.counting import count_document
    expected=count_document("alice","rabbit")
    workers=Counter()
    for index in range(args.requests):
        row=request(args.host,args.port,"alice","rabbit",expected,time.perf_counter())
        if not row['ok']:parser.exit(1,f"Query failed: {row}\n")
        workers[row['server_id']]+=1
        if not args.summary_only:
            print(f"query={index+1} worker={row['server_id']} count={expected} cache={row['cache_status']}",flush=True)
        if args.interval:time.sleep(args.interval)
    print(f"Worker query totals: {dict(workers)}")
    if len(workers)<3:
        print("Fewer than three workers observed. Inspect the proxy status and logs.")


if __name__=="__main__":
    main()
