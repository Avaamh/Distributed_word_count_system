"""Inspect proxy counters without opening a proxied worker connection."""
import argparse
import json
import urllib.request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host",default="balancer")
    parser.add_argument("--port",type=int,default=18862)
    args=parser.parse_args()
    try:
        # Internal Docker addresses should be accessed directly, not via a
        # workstation's HTTP proxy environment variables.
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://{args.host}:{args.port}/status",timeout=2) as response:
            data=json.load(response)
        print(json.dumps(data,indent=2))
    except (OSError,ValueError) as exc:
        parser.exit(1,f"Status error: {exc}\n")


if __name__=="__main__":
    main()
