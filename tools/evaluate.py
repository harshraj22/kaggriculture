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
import tempfile
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import load_env
from tracking import log_record

load_env()

RESULTS = ROOT / "results" / "experiments.jsonl"
TRAJECTORIES = ROOT / "results" / "trajectories"
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


def env_version() -> str:
    """Version of kaggle-environments — the THIRD thing that can invalidate a result.

    `code_hash` covers agentlib and `protocol_hash` covers the measurement, but the
    environment itself can change underneath both. 1.32.7 switched CARROT, TOMATO
    and EGG scarcity curves to a new `hinge` shape and dropped a constant: every
    number measured on 1.32.2 describes a different game. Without this recorded,
    that difference is invisible.
    """
    try:
        import kaggle_environments

        return getattr(kaggle_environments, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def env_hash() -> str:
    """Hash the kaggriculture env source.

    Stronger than the version string, which comes from package metadata and can
    disagree with the files actually on disk (it did in development: metadata said
    1.25.9 while the source was 1.32.2). Contents can't lie.
    """
    try:
        from kaggle_environments.envs.kaggriculture import kaggriculture as env_mod

        return _hash_text(Path(env_mod.__file__).read_text())
    except Exception:  # noqa: BLE001
        return "unknown"


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
    seed, opponent, seat, steps, spec = job

    # Fresh interpreter state per call is not guaranteed inside a pool worker,
    # so reset explicitly. Without this, episode N inherits episode N-1's
    # strikes and journal and the measurement is contaminated.
    from agentlib import planner

    # Re-read rather than trusting the module-level constant: `agentlib` is
    # usually already imported by the time we get here, so the constant was
    # resolved before evaluate() set the env var.
    planner.RECORD_TRAJECTORY = bool(os.environ.get("KAGGRICULTURE_RECORD_TRAJECTORY"))
    planner.reset()

    # The spec is passed by value, not via a file path, so an Optuna trial's
    # in-memory config works without ever touching disk. Written to a temp file
    # because the agent is loaded in this same process by kaggle_environments
    # and reads its config through the env var.
    tmp = None
    if spec is not None:
        fd, tmp = tempfile.mkstemp(suffix=".json", prefix="kaggr_spec_")
        with os.fdopen(fd, "w") as f:
            json.dump({k: v for k, v in spec.items() if not k.startswith("_")}, f)
        os.environ["KAGGRICULTURE_CONFIG"] = tmp
    else:
        # No spec: make sure a previous trial's config can't leak into this one.
        os.environ.pop("KAGGRICULTURE_CONFIG", None)

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

    if tmp:
        os.unlink(tmp)

    episode = {
        "seed": seed,
        "opponent": opponent,
        "seat": seat,
        "ours": ours,
        "theirs": theirs,
        "margin": (ours - theirs) if (ours is not None and theirs is not None) else None,
        "status": status,
        "wall": round(time.time() - t0, 2),
    }
    if planner.RECORD_TRAJECTORY and planner._AGENT is not None:
        episode["trajectory"] = planner._AGENT.journal
    return episode


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


def evaluate(
    config_path=None,
    protocol_path=DEFAULT_PROTOCOL,
    split="train",
    jobs=None,
    note="",
    spec=None,
    study=None,
    trial=None,
    record_trajectory=False,
    wandb=False,
):
    """Score one controller against one protocol; append the result to the store.

    Supply either `config_path` (a file) or `spec` (an in-memory dict). The spec
    path is what an Optuna trial uses — it proposes a config that never exists on
    disk, so requiring a file would mean writing and deleting a YAML per trial.

    `study` / `trial` are recorded so a sweep's own storage can be joined against
    our provenance records later.
    """
    from agentlib.settings import load_spec, spec_hash

    proto = load_protocol(Path(protocol_path))
    seeds = proto["seeds"][split]
    steps = proto.get("episode_steps", 720)

    if spec is None:
        # Strict: a typo must fail before we spend minutes measuring the wrong thing.
        spec = load_spec(config_path, strict=True)

    from agentlib.controllers import build_controller
    from agentlib.strategies import build_all

    known = {s.name for s in build_all()}
    controller = build_controller(spec, known=known, strict=True)
    # Mirrors Agent.action_space — the index->strategy mapping a trained policy
    # will need, stored with the trajectories so it can't drift away from them.
    action_space = sorted(known)

    seats = [0, 1] if proto.get("swap_seats") else [0]
    jobs_list = [
        (seed, opp, seat, steps, spec)
        for seed in seeds
        for opp in proto["opponents"]
        for seat in seats
    ]

    t0 = time.time()
    workers = jobs or min(os.cpu_count() or 1, 8)
    if record_trajectory:
        # Trajectories are per-process state; keep it single-process so they
        # come back in a defined order and nothing is dropped by pickling.
        os.environ["KAGGRICULTURE_RECORD_TRAJECTORY"] = "1"
        workers = 1
    try:
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                episodes = list(pool.map(play, jobs_list))
        else:
            episodes = [play(j) for j in jobs_list]
    finally:
        if record_trajectory:
            os.environ.pop("KAGGRICULTURE_RECORD_TRAJECTORY", None)

    rev, dirty = git_state()
    record = {
        "run_id": uuid.uuid4().hex[:12],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": note,
        # None for an in-memory spec (an Optuna trial); the config_hash is the
        # real identity either way.
        "config_path": str(config_path) if config_path else None,
        "config_hash": spec_hash(spec),
        "controller": controller.describe(),
        "protocol_id": proto["id"],
        "protocol_hash": proto["_hash"],
        "split": split,
        "code_hash": code_hash(),
        "env_version": env_version(),
        "env_hash": env_hash(),
        "git_rev": rev,
        "git_dirty": dirty,
        "study": study,
        "trial": trial,
        "wall": round(time.time() - t0, 1),
        "summary": summarise(episodes),
        "episodes": episodes,
    }

    # Trajectories are ~130 KB per episode — writing them inline would grow the
    # results store by ~8 MB per experiment and make it unreadable. They live in
    # their own file, referenced by run_id.
    trajectories = [(e.pop("trajectory"), e) for e in episodes if "trajectory" in e]
    if trajectories:
        path = TRAJECTORIES / f"{record['run_id']}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for traj, ep in trajectories:
                f.write(json.dumps({
                    "seed": ep["seed"],
                    "opponent": ep["opponent"],
                    "seat": ep["seat"],
                    # The terminal reward. Credit assignment across 720 decisions
                    # from this single number is the trainer's problem.
                    "reward": ep["ours"],
                    "action_space": action_space,
                    "transitions": traj,
                }) + "\n")
        record["trajectories"] = str(path.relative_to(ROOT))

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a") as f:
        f.write(json.dumps(record) + "\n")

    # After the write, never before: the JSONL is the source of truth and a
    # tracking outage must not cost a measurement.
    url = log_record(record, flag=wandb)
    if url:
        record["wandb_url"] = url
    return record


#: What a sweep optimises. Two defensible choices, and they disagree:
#:
#: `mean_margin` — continuous and lower-variance than win rate, which is binary and
#:   would make BO burn trials on noise. Blind to dispersion by construction.
#:
#: `margin_z` — `mean_margin / stdev_margin`. The ladder scores win/loss, and the
#:   argument circulating in the competition forum is that the real objective is
#:   `Pr[win] = Φ(μ/σ)`, in which **σ is part of the objective, not noise to average
#:   away**. Two configs with equal mean are not equally good; the tighter one wins
#:   more often. The 1.32.7 balance patch is the proof case — it added ~0 at the
#:   median and a lot in the tail, a pure dispersion change `mean_margin` cannot see.
#:
#: Defaulting to `mean_margin` because `stdev_margin` over 60 paired episodes is
#: itself a noisy estimate. Revisit once a protocol with more episodes exists.
OBJECTIVE = "mean_margin"

#: Floor on σ for `margin_z`: a config that never loses would otherwise divide by
#: ~0 and score unbounded, which BO will chase straight off a cliff.
MIN_STDEV = 1.0


def score(summary: dict, objective_name: str | None = None) -> float:
    """Reduce a summary to the single number a sweep maximises."""
    name = objective_name or OBJECTIVE
    if not summary.get("n"):
        return float("-inf")
    if name == "margin_z":
        return float(summary["mean_margin"]) / max(summary.get("stdev_margin", 0.0), MIN_STDEV)
    return float(summary[name])


def objective(spec: dict, objective_name: str | None = None, **kw) -> float:
    """Scalar objective for Optuna: `evaluate` reduced to one number.

    An all-errored config returns -inf rather than raising, so one bad proposal
    can't kill a study mid-sweep.

        study.optimize(lambda t: objective(spec_from(t), study=t.study.study_name,
                                           trial=t.number), n_trials=200)
    """
    return score(evaluate(spec=spec, **kw)["summary"], objective_name)


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--config", help="a configs/*.yaml file")
    src.add_argument("--strategy", help="measure one strategy in isolation (builds a fixed spec)")
    ap.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    ap.add_argument("--split", default="train", choices=["train", "holdout"])
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--note", default="")
    ap.add_argument("--wandb", action="store_true",
                    help="mirror this result into Weights & Biases (or set KAGGRICULTURE_WANDB=1)")
    ap.add_argument("--trajectory", action="store_true",
                    help="record RL transitions (features/action/mask); forces 1 worker")
    args = ap.parse_args()

    spec = None
    if args.strategy:
        from agentlib.controllers.fixed import spec_for

        spec = spec_for(args.strategy)

    rec = evaluate(
        args.config, args.protocol, args.split, args.jobs, args.note,
        spec=spec, record_trajectory=args.trajectory, wandb=args.wandb,
    )
    s = rec["summary"]
    label = args.config or f"strategy:{args.strategy}"
    print(f"run {rec['run_id']}  {label}  protocol={rec['protocol_id']}/{args.split}")
    print(f"  code={rec['code_hash']}{'*' if rec['git_dirty'] else ''}  "
          f"config={rec['config_hash']}  env={rec['env_version']}/{rec['env_hash'][:6]}")
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
    print(f"  objective: mean_margin={score(s, 'mean_margin'):+.1f}  "
          f"margin_z={score(s, 'margin_z'):+.2f}")
    print(f"  -> {RESULTS.relative_to(ROOT)}  ({rec['wall']}s)")
    if rec.get("wandb_url"):
        print(f"  -> {rec['wandb_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
