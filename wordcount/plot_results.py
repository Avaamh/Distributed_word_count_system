"""Regenerate benchmark figures from a saved summary.csv."""
import argparse
import csv
from pathlib import Path
from wordcount.benchmark import make_plots


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path)
    args=parser.parse_args()
    with (args.directory/"summary.csv").open(newline="",encoding="utf-8") as f:
        rows=list(csv.DictReader(f))
    for row in rows:
        row["rate_rps"]=int(row["rate_rps"])
        for metric in ("mean_rpc_ms","p99_rpc_ms","failure_percent"):
            row[metric]=float(row[metric]) if row[metric] else None
    make_plots(rows,args.directory)
    print(f"Regenerated figures in {args.directory}")


if __name__=="__main__":
    main()
