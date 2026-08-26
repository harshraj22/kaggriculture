#!/usr/bin/env python
"""Backfill results/experiments.jsonl into Weights & Biases.

    python tools/sync_wandb.py                    # everything not yet synced
    python tools/sync_wandb.py --protocol v1      # one protocol
    python tools/sync_wandb.py --all              # re-sync, overwriting

Runs are created with `id=run_id`, so re-syncing updates rather than duplicates.
That's what makes the JSONL the source of truth: delete the W&B project, run this,
and you're whole again.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import load_env

load_env()

from compare import load
from tracking import log_record

SYNCED = ROOT / "results" / ".wandb_synced"


def read_synced() -> set[str]:
    if not SYNCED.exists():
        return set()
    return {ln.strip() for ln in SYNCED.read_text().splitlines() if ln.strip()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", default=None)
    ap.add_argument("--project", default=None)
    ap.add_argument("--all", action="store_true", help="re-sync already-synced runs")
    args = ap.parse_args()

    runs = load()
    if args.protocol:
        runs = [r for r in runs if r["protocol_id"] == args.protocol]
    if not runs:
        print("nothing to sync")
        return 0

    already = set() if args.all else read_synced()
    todo = [r for r in runs if r["run_id"] not in already]
    print(f"{len(todo)} run(s) to sync ({len(runs) - len(todo)} already done)")

    done = []
    for r in todo:
        url = log_record(r, project=args.project, flag=True)
        if url is None:          # None means failed; "" means offline (fine)
            print("  aborted — wandb unavailable or erroring")
            break
        done.append(r["run_id"])
        print(f"  {r['run_id']}  {url or '(offline)'}")

    if done:
        with SYNCED.open("a") as f:
            f.write("\n".join(done) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
