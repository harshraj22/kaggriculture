#!/usr/bin/env python
"""Read the results store and tabulate experiments.

    python tools/compare.py                        # latest run per config
    python tools/compare.py --all                  # every run
    python tools/compare.py --protocol v1 --split holdout
    python tools/compare.py --vs '[safe_farmer]'   # PAIRED deltas vs a reference

The guard rails are the point:

* Runs from different **protocols** are never ranked together — they measured
  different things, so the comparison is meaningless rather than merely noisy.
* Runs from different **code_hash** values are flagged. `wheat_loop` today is not
  `wheat_loop` last week, and silently ranking across that is how you conclude a
  config helped when actually a strategy got fixed underneath it.
* A dirty git tree marks the row, because that result isn't reproducible.

The `--vs` mode is not a cosmetic extra. Every config plays the **same seeds**, so
when you ask "is A better than B" the seed-to-seed noise is common to both and
cancels. The `sd` column in the main table is *marginal* — the spread of one
config's own results — and it answers "how good is this agent, absolutely". Using
it to judge a difference between two configs overstates the uncertainty, often by
a lot. `--vs` computes the per-seed difference and reports its spread instead,
which is the number that governs whether a ranking is real.
"""

import argparse
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from math import sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "experiments.jsonl"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import load_env

load_env()


def label(run: dict) -> str:
    """A short name for a run.

    `config_path` is None for spec-based runs (an Optuna trial, or --strategy),
    so fall back to describing the controller itself."""
    if run.get("config_path"):
        return Path(run["config_path"]).stem
    ctrl = run.get("controller") or {}
    if ctrl.get("type") == "fixed":
        return f"[{ctrl['strategy']}]"
    if run.get("trial") is not None:
        return f"{run.get('study', 'trial')}#{run['trial']}"
    return f"<{ctrl.get('type', 'spec')}:{run['config_hash'][:6]}>"


def _by_episode(run: dict) -> dict:
    """Margins keyed by (seed, seat) — the unit that is common across runs."""
    return {
        (e["seed"], e["seat"]): e["margin"]
        for e in run.get("episodes", [])
        if e.get("status") == "DONE" and e.get("margin") is not None
    }


def paired(a: dict, b: dict) -> dict | None:
    """Per-seed difference a - b, over the episodes the two runs share.

    Returns None if they overlap on fewer than two episodes — with one point
    there is no spread to report and a delta would look far more certain than
    it is. `rho` is the correlation between the two runs' per-seed margins: it
    is what pairing buys, and it is high exactly when the environment's noise
    hits both configs the same way.
    """
    ma, mb = _by_episode(a), _by_episode(b)
    common = sorted(set(ma) & set(mb))
    if len(common) < 2:
        return None

    xs = [ma[k] for k in common]
    ys = [mb[k] for k in common]
    d = [x - y for x, y in zip(xs, ys)]
    n = len(d)

    sd_a, sd_b = st.stdev(xs), st.stdev(ys)
    sd_d = st.stdev(d)
    try:
        rho = st.correlation(xs, ys)
    except (st.StatisticsError, ZeroDivisionError):
        rho = None  # one side is constant across seeds; correlation undefined

    # What you would have concluded treating the two runs as independent samples.
    # Printed alongside the paired figure so the difference is visible, not asserted.
    se_unpaired = sqrt(sd_a**2 / n + sd_b**2 / n)

    return {
        "n": n,
        "delta": st.mean(d),
        "sd": sd_d,
        "se": sd_d / sqrt(n),
        "se_unpaired": se_unpaired,
        "rho": rho,
        "identical": sd_d == 0.0 and st.mean(d) == 0.0,
    }


def print_paired(ref: dict, runs: list[dict]) -> None:
    """Paired deltas of every run against `ref`, on the seeds they share.

    Restricted to `ref`'s split: train and holdout seeds are disjoint by
    construction, so pairing across them would find zero overlap every time and
    report it as if something had gone wrong.
    """
    split = ref.get("split")
    runs = [r for r in runs if r.get("split") == split]
    print(f"\npaired against {label(ref)} ({split})   "
          "(per-seed differences; common noise cancels)\n")
    hdr = (f"{'config':<26} {'n':>3} {'delta':>8} {'sd_p':>7} {'se_p':>7} "
           f"{'95% CI':>18} {'rho':>6}  {'se if unpaired':>14}")
    print(hdr)
    print("-" * len(hdr))

    shown = 0
    for r in runs:
        if r["run_id"] == ref["run_id"]:
            continue
        shown += 1
        if r.get("env_hash") != ref.get("env_hash"):
            print(f"{label(r):<26}  skipped — different environment build")
            continue
        if r.get("code_hash") != ref.get("code_hash"):
            print(f"{label(r):<26}  skipped — different code_hash")
            continue

        p = paired(r, ref)
        if p is None:
            print(f"{label(r):<26}  no shared episodes with the reference")
            continue
        if p["identical"]:
            print(f"{label(r):<26} {p['n']:>3}   identical on every shared seed")
            continue

        lo, hi = p["delta"] - 1.96 * p["se"], p["delta"] + 1.96 * p["se"]
        rho = f"{p['rho']:>6.2f}" if p["rho"] is not None else "     -"
        print(
            f"{label(r):<26} {p['n']:>3} {p['delta']:>+8.0f} {p['sd']:>7.1f} {p['se']:>7.1f} "
            f"{f'[{lo:+.0f}, {hi:+.0f}]':>18} {rho}  {p['se_unpaired']:>14.1f}"
        )

    if not shown:
        print(f"(nothing else was run on {split} under this protocol)")

    print("\nse_p is the honest error bar for a RANKING; the last column is what you"
          "\nwould get treating the runs as independent. When rho is high the two"
          "\ndiverge sharply and the marginal `sd` above will mislead you.")


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
    ap.add_argument(
        "--vs",
        nargs="?",
        const=True,
        default=None,
        metavar="REF",
        help="paired deltas against REF (a config label); bare --vs uses the top row",
    )
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
            print(f"{label(r):<26} {r.get('split', ''):<8}  ALL EPISODES ERRORED")
            continue
        by_code[r["code_hash"]].append(r)
        mark = "*" if r.get("git_dirty") else " "
        print(
            f"{label(r):<26} {r.get('split', ''):<8} {s['n']:>3} "
            f"{s['win_rate'] * 100:>5.1f}% {s['mean_margin']:>+9.0f} {s['stdev_margin']:>7.0f} "
            f"{s['mean_score']:>8.0f}  {r['code_hash']}{mark}"
        )

    if args.vs is not None:
        ranked = [r for r in scoped if r["summary"].get("n")]
        if args.vs is True:
            # Best run in the *most-populated* split, not simply the top row: the
            # table sorts across splits, so a lone holdout run can outrank the
            # train runs and become a reference nothing else can pair with.
            splits = Counter(r.get("split") for r in ranked)
            main_split = splits.most_common(1)[0][0] if splits else None
            pool = [r for r in ranked if r.get("split") == main_split]
            ref = pool[0] if pool else None
        else:
            ref = next((r for r in ranked if label(r) == args.vs), None)
            if ref is None:
                print(f"\n!! no run labelled {args.vs!r}. Available: "
                      f"{', '.join(sorted({label(r) for r in ranked}))}")
                return 1
        if ref is not None:
            print_paired(ref, ranked)

    envs = {(r.get("env_version", "unknown"), r.get("env_hash", "unknown")) for r in scoped}
    if len(envs) > 1:
        pretty = sorted(f"{v}/{h[:6]}" for v, h in envs)
        print(f"\n!! {len(envs)} different environment builds: {pretty}")
        print("!! The ENVIRONMENT changed between these runs — different game rules,")
        print("!! not just different code. These rows are NOT comparable at all.")
        print("!! (1.32.7 changed CARROT/TOMATO/EGG scarcity curves, for example.)")

    if len(by_code) > 1:
        print(f"\n!! {len(by_code)} different code versions in this table:")
        for h, rs in sorted(by_code.items(), key=lambda kv: -len(kv[1])):
            names = ", ".join(sorted({label(r) for r in rs}))
            print(f"!!   {h}: {names}")
        print("!! Strategy code changed between these runs, so differences are NOT")
        print("!! attributable to config alone. Re-run the older configs to compare.")

    if any(r.get("git_dirty") for r in scoped):
        print("\n*  = uncommitted changes when the run happened; not reproducible.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
