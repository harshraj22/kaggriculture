#!/usr/bin/env python
"""Read the results store and tabulate experiments.

    python tools/compare.py                        # latest run per config
    python tools/compare.py --all                  # every run
    python tools/compare.py --protocol v1 --split holdout

The guard rails are the point:

* Runs from different **protocols** are never ranked together — they measured
  different things, so the comparison is meaningless rather than merely noisy.
* Runs from different **code_hash** values are flagged. `wheat_loop` today is not
  `wheat_loop` last week, and silently ranking across that is how you conclude a
  config helped when actually a strategy got fixed underneath it.
* A dirty git tree marks the row, because that result isn't reproducible.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "experiments.jsonl"


def load(path=RESULTS) -> list[dict]:
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(RESULTS))
    ap.add_argument("--protocol", default=None, help="protocol id; default = most recent")
    ap.add_argument("--split", default=None)
    ap.add_argument("--all", action="store_true", help="every run, not just the latest per config")
    ap.add_argument("--sort", default="mean_margin", choices=["mean_margin", "win_rate", "mean_score"])
    args = ap.parse_args()

    runs = load(args.results)
    if not runs:
        print(f"no results yet at {args.results}")
        print("run:  python tools/evaluate.py --config configs/baseline.yaml")
        return 0

    protocol = args.protocol or runs[-1]["protocol_id"]
    scoped = [r for r in runs if r["protocol_id"] == protocol]
    if args.split:
        scoped = [r for r in scoped if r.get("split") == args.split]
    if not scoped:
        print(f"no runs for protocol={protocol} split={args.split}")
        return 1

    dropped = len(runs) - len([r for r in runs if r["protocol_id"] == protocol])
    if dropped:
        others = sorted({r["protocol_id"] for r in runs if r["protocol_id"] != protocol})
        print(f"note: hiding {dropped} run(s) from other protocols ({', '.join(others)}) — "
              "different protocols are not comparable.\n")

    # Same protocol id but different content = someone edited it in place.
    hashes = {r["protocol_hash"] for r in scoped}
    if len(hashes) > 1:
        print(f"!! protocol '{protocol}' has {len(hashes)} different content hashes.")
        print("!! It was edited in place instead of being versioned. These rows are NOT")
        print("!! comparable; bump the protocol id and re-measure.\n")

    if not args.all:
        latest: dict = {}
        for r in scoped:
            key = (r["config_hash"], r.get("split"))
            if key not in latest or r["timestamp"] > latest[key]["timestamp"]:
                latest[key] = r
        scoped = list(latest.values())

    scoped.sort(key=lambda r: r["summary"].get(args.sort, float("-inf")), reverse=True)

    print(f"protocol={protocol}   sorted by {args.sort}\n")
    hdr = f"{'config':<26} {'split':<8} {'n':>3} {'win%':>6} {'margin':>9} {'sd':>7} {'score':>8}  code"
    print(hdr)
    print("-" * len(hdr))

    by_code = defaultdict(list)
    for r in scoped:
        s = r["summary"]
        if not s.get("n"):
            print(f"{Path(r['config_path']).stem:<26} {r.get('split', ''):<8}  ALL EPISODES ERRORED")
            continue
        by_code[r["code_hash"]].append(r)
        mark = "*" if r.get("git_dirty") else " "
        print(
            f"{Path(r['config_path']).stem:<26} {r.get('split', ''):<8} {s['n']:>3} "
            f"{s['win_rate'] * 100:>5.1f}% {s['mean_margin']:>+9.0f} {s['stdev_margin']:>7.0f} "
            f"{s['mean_score']:>8.0f}  {r['code_hash']}{mark}"
        )

    if len(by_code) > 1:
        print(f"\n!! {len(by_code)} different code versions in this table:")
        for h, rs in sorted(by_code.items(), key=lambda kv: -len(kv[1])):
            names = ", ".join(sorted({Path(r["config_path"]).stem for r in rs}))
            print(f"!!   {h}: {names}")
        print("!! Strategy code changed between these runs, so differences are NOT")
        print("!! attributable to config alone. Re-run the older configs to compare.")

    if any(r.get("git_dirty") for r in scoped):
        print("\n*  = uncommitted changes when the run happened; not reproducible.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
