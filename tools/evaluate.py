#!/usr/bin/env python
"""Score one config against one protocol; append the result to the store.

    python tools/evaluate.py --config configs/baseline.yaml
    python tools/evaluate.py --config configs/baseline.yaml --split holdout
    python tools/evaluate.py --config configs/baseline.yaml --jobs 8 --note "after routing fix"

This is also the objective function a Bayesian optimiser will call.

Comparability rests on three hashes recorded with every result:

* `config_hash`  — content of the *resolved* spec, so renaming a file doesn't
                   invent a new experiment.
* `protocol_hash`— the measurement contract. compare.py refuses to rank across
                   differing values.
* `code_hash`    — the contents of agentlib/. `wheat_loop` today is not
                   `wheat_loop` next week, and a score without this is
                   uninterpretable later.
"""

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS = ROOT / "results" / "experiments.jsonl"
DEFAULT_PROTOCOL = ROOT / "eval" / "protocols" / "v1.yaml"


# --- provenance ---------------------------------------------------------------


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def code_hash() -> str:
    """Hash every .py under agentlib/. Catches uncommitted edits, which git rev won't."""
    h = hashlib.sha256()
    for path in sorted((ROOT / "agentlib").rglob("*.py")):
        h.update(path.relative_to(ROOT).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def git_state() -> tuple[str, bool]:
    def run(*args):
        return subprocess.run(
            args, cwd=ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()

    rev = run("git", "rev-parse", "--short", "HEAD") or "unknown"
    dirty = bool(run("git", "status", "--porcelain"))
    return rev, dirty


def load_protocol(path: Path) -> dict:
    text = path.read_text()
    if path.suffix == ".json":
        proto = json.loads(text)
    else:
        import yaml

        proto = yaml.safe_load(text)
    proto["_hash"] = _hash_text(
        json.dumps({k: v for k, v in proto.items() if k != "_hash"}, sort_keys=True)
    )
    return proto


# --- one episode --------------------------------------------------------------


def play(job) -> dict:
    """Run a single episode. Executed in a worker process."""
    seed, opponent, seat, steps, config_path = job

    # Fresh interpreter state per call is not guaranteed inside a pool worker,
    # so reset explicitly. Without this, episode N inherits episode N-1's
    # strikes and journal and the measurement is contaminated.
    from agentlib import planner

    planner.reset()
    if config_path:
        os.environ["KAGGRICULTURE_CONFIG"] = str(config_path)

    from kaggle_environments import make

    agent_path = str(ROOT / "main.py")
    players = [agent_path, opponent] if seat == 0 else [opponent, agent_path]

    env = make("kaggriculture", configuration={"episodeSteps": steps, "seed": seed})
    t0 = time.time()
    env.run(players)
    final = env.steps[-1]

    ours = final[seat].get("reward")
    theirs = final[1 - seat].get("reward")
    status = final[seat].get("status")
    return {
        "seed": seed,
        "opponent": opponent,
        "seat": seat,
        "ours": ours,
        "theirs": theirs,
        "margin": (ours - theirs) if (ours is not None and theirs is not None) else None,
        "status": status,
        "wall": round(time.time() - t0, 2),
    }


# --- aggregation --------------------------------------------------------------


def wilson(wins: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    d = 1 + z**2 / n
    c = p + z**2 / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return round((c - s) / d, 4), round((c + s) / d, 4)


def summarise(episodes: list[dict]) -> dict:
    ok = [e for e in episodes if e["status"] == "DONE" and e["margin"] is not None]
    errors = len(episodes) - len(ok)
    if not ok:
        return {"n": 0, "errors": errors}

    margins = [e["margin"] for e in ok]
    scores = [e["ours"] for e in ok]
    wins = sum(1 for m in margins if m > 0)
    ties = sum(1 for m in margins if m == 0)
    lo, hi = wilson(wins, len(ok))
    return {
        "n": len(ok),
        "errors": errors,
        "wins": wins,
        "losses": len(ok) - wins - ties,
        "ties": ties,
        "win_rate": round(wins / len(ok), 4),
        "wilson_lo": lo,
        "wilson_hi": hi,
        "mean_margin": round(statistics.mean(margins), 1),
        "median_margin": round(statistics.median(margins), 1),
        "mean_score": round(statistics.mean(scores), 1),
        # Reported so a sweep can tell "config is better" from "seeds were kind".
        "stdev_margin": round(statistics.stdev(margins), 1) if len(margins) > 1 else 0.0,
    }


# --- driver -------------------------------------------------------------------


def evaluate(config_path, protocol_path=DEFAULT_PROTOCOL, split="train", jobs=None, note=""):
    from agentlib.settings import load_spec, spec_hash

    proto = load_protocol(Path(protocol_path))
    seeds = proto["seeds"][split]
    steps = proto.get("episode_steps", 720)

    # Strict: a typo here must fail before we spend minutes measuring the wrong thing.
    spec = load_spec(config_path, strict=True)
    from agentlib.controllers import build_controller
    from agentlib.strategies import build_all

    known = {s.name for s in build_all()}
    controller = build_controller(spec, known=known, strict=True)

    seats = [0, 1] if proto.get("swap_seats") else [0]
    jobs_list = [
        (seed, opp, seat, steps, config_path)
        for seed in seeds
        for opp in proto["opponents"]
        for seat in seats
    ]

    t0 = time.time()
    workers = jobs or min(os.cpu_count() or 1, 8)
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            episodes = list(pool.map(play, jobs_list))
    else:
        episodes = [play(j) for j in jobs_list]

    rev, dirty = git_state()
    record = {
        "run_id": uuid.uuid4().hex[:12],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": note,
        "config_path": str(config_path),
        "config_hash": spec_hash(spec),
        "controller": controller.describe(),
        "protocol_id": proto["id"],
        "protocol_hash": proto["_hash"],
        "split": split,
        "code_hash": code_hash(),
        "git_rev": rev,
        "git_dirty": dirty,
        "wall": round(time.time() - t0, 1),
        "summary": summarise(episodes),
        "episodes": episodes,
    }

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    ap.add_argument("--split", default="train", choices=["train", "holdout"])
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    rec = evaluate(args.config, args.protocol, args.split, args.jobs, args.note)
    s = rec["summary"]
    print(f"run {rec['run_id']}  {args.config}  protocol={rec['protocol_id']}/{args.split}")
    print(f"  code={rec['code_hash']}{'*' if rec['git_dirty'] else ''}  config={rec['config_hash']}")
    if not s.get("n"):
        print(f"  ALL {s['errors']} EPISODES ERRORED")
        return 1
    print(
        f"  n={s['n']} errors={s['errors']}  win={s['win_rate']:.1%} "
        f"[{s['wilson_lo']:.1%},{s['wilson_hi']:.1%}]"
    )
    print(
        f"  margin mean={s['mean_margin']:+.0f} median={s['median_margin']:+.0f} "
        f"sd={s['stdev_margin']:.0f}   our score={s['mean_score']:.0f}"
    )
    print(f"  -> {RESULTS.relative_to(ROOT)}  ({rec['wall']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
