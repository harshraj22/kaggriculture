#!/usr/bin/env python
"""Score one config against one protocol; append the result to the store.

    python tools/evaluate.py --config configs/baseline.yaml
    python tools/evaluate.py --config configs/baseline.yaml --split holdout
    python tools/evaluate.py --config configs/baseline.yaml --note "after routing fix"

`--jobs` defaults to usable cores minus one, so it does not need setting per
machine; `$KAGGRICULTURE_JOBS` pins it for a box that wants something else.

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

#: Upper bound on auto-detected workers. Not a performance limit — episodes are
#: pure CPU and scale linearly — but each worker holds its own interpreter and
#: `kaggle_environments` import, so an unbounded pool on a very large machine
#: trades memory for throughput nobody asked for. Override with `--jobs`.
MAX_AUTO_JOBS = 16


def default_jobs() -> int:
    """Workers to use when `--jobs` is not given: usable cores minus one.

    Minus one, not all of them. The parent process is not idle while the pool
    runs — it collects and summarises every episode — and the OS still needs to
    schedule it. Running one worker per core produced repeated multi-minute
    stalls on a 4-core box earlier in this project, and `--jobs 3` there was the
    fix. Making that the default means nobody has to rediscover it.

    `sched_getaffinity` before `cpu_count` because they disagree exactly where it
    matters: inside a container or under `taskset`, `cpu_count` reports the
    host's cores and the pool oversubscribes its actual allocation.
    """
    override = os.environ.get("KAGGRICULTURE_JOBS")
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            print(f"[tools] ignoring non-numeric KAGGRICULTURE_JOBS={override!r}")

    cores = None
    if hasattr(os, "sched_getaffinity"):  # Linux; absent on macOS
        try:
            cores = len(os.sched_getaffinity(0))
        except OSError:
            cores = None
    cores = cores or os.cpu_count() or 1
    return max(1, min(cores - 1, MAX_AUTO_JOBS))


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


# --- opponents ----------------------------------------------------------------
#
# A protocol names its opponents as strings. Three forms:
#
#   pass | random | starter    a built-in the env resolves by name; frozen forever
#   self                       our entrypoint on both seats, sharing ONE config,
#                              so it is necessarily a mirror of whatever is under test
#   strategy:<name>            our entrypoint pinned to one strategy
#   config:<path>              our entrypoint pinned to a config file
#   file:<path>                a FOREIGN agent — someone else's main.py
#
# The last two exist because `starter` stopped discriminating: once a strategy
# beats it in 60 of 60 games, `win_rate` saturates and two strategies 12,000 coins
# apart score identically. A pinned in-repo opponent is as strong as our best work
# and keeps the metric informative.
#
# It is a LIVE benchmark: the opponent is whatever that strategy is today, so
# improving it moves the yardstick. `code_hash` records this and `compare.py`
# already refuses to rank across differing values, which is the guard that makes
# the choice safe rather than merely convenient.

# The `file:` form is what makes the local benchmark mean anything. Our own
# strategies are the only opponents we can otherwise field, so v3 grades us
# against ourselves; a public competitor agent grades us against the meta. It
# runs THIRD-PARTY CODE in this process — keep such agents under `opponents/`
# (gitignored), never import them from `agentlib/`, and verify the checksum the
# author published before trusting a run against them.
BUILTIN_OPPONENTS = ("pass", "random", "starter")
PINNED_PREFIXES = ("strategy:", "config:", "file:")


def parse_opponent(opponent: str) -> tuple[str, str | None]:
    """Split an opponent string into (kind, value). Raises on anything unknown.

    Called from `evaluate()` before any episode runs: a typo here would otherwise
    surface as `FileNotFoundError: Could not find : wheat_farmm` after the pool has
    already been spun up, or worse, as a silently different matchup.
    """
    if opponent in BUILTIN_OPPONENTS:
        return ("builtin", opponent)
    if opponent == "self":
        return ("self", None)
    for prefix in PINNED_PREFIXES:
        if opponent.startswith(prefix):
            value = opponent[len(prefix):].strip()
            if not value:
                raise ValueError(f"opponent {opponent!r} has an empty {prefix[:-1]} name")
            return (prefix[:-1], value)
    raise ValueError(
        f"unknown opponent {opponent!r}; expected one of {BUILTIN_OPPONENTS}, "
        "'self', 'strategy:<name>', 'config:<path>' or 'file:<path>'"
    )


def _pinned_config(kind: str, value: str) -> tuple[str, str | None]:
    """Resolve a pinned opponent to a config path. Returns (path, tempfile_or_None)."""
    if kind == "config":
        path = Path(value)
        return (str(path if path.is_absolute() else ROOT / path), None)

    from agentlib.controllers.fixed import spec_for

    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="kaggr_opp_")
    with os.fdopen(fd, "w") as f:
        json.dump(spec_for(value), f)
    return (tmp, tmp)


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

    # Pool workers are REUSED across episodes. A seat-pinned config left behind by
    # the previous job would silently change who the next episode plays — a `self`
    # mirror inheriting a pin stops being a mirror, and nothing in the result would
    # say so. Clear both seats every time.
    for s in (0, 1):
        os.environ.pop(f"KAGGRICULTURE_CONFIG_{s}", None)

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
    # "self" is a mirror match: the same entrypoint on both seats, reading the same
    # config. Both players run in ONE interpreter, so this only measures anything
    # because planner keeps an Agent per seat rather than a module-level singleton.
    kind, value = parse_opponent(opponent)
    opp_tmp = None
    if kind == "builtin":
        opp_path = value
    elif kind == "file":
        # A foreign agent: its own file, its own module namespace, no config of
        # ours. `kaggle_environments` loads it exactly as it loads ours.
        fp = Path(value)
        opp_path = str(fp if fp.is_absolute() else ROOT / fp)
        if not Path(opp_path).exists():
            raise FileNotFoundError(
                f"opponent agent {opp_path} not found. Fetch it first — see "
                "tools/extract_agent.py"
            )
    else:
        opp_path = agent_path          # our entrypoint drives the other seat too
        if kind != "self":
            # `self` deliberately shares our process-wide config (a mirror). A
            # pinned opponent gets its own seat-indexed one, which takes priority
            # in load_spec while our seat still falls through to the shared var.
            target, opp_tmp = _pinned_config(kind, value)
            os.environ[f"KAGGRICULTURE_CONFIG_{1 - seat}"] = target
    players = [agent_path, opp_path] if seat == 0 else [opp_path, agent_path]

    env = make("kaggriculture", configuration={"episodeSteps": steps, "seed": seed})
    t0 = time.time()
    env.run(players)
    final = env.steps[-1]

    ours = final[seat].get("reward")
    theirs = final[1 - seat].get("reward")
    status = final[seat].get("status")

    if tmp:
        os.unlink(tmp)
    if opp_tmp:
        os.unlink(opp_tmp)

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
    ours_agent = planner.agent_for(seat)
    if ours_agent is not None:
        # From the worker's controller, which actually played. The parent process
        # builds a controller too, but only to validate the spec — it never sees a
        # turn, so reading runtime counters off it reports the constructor's zeros.
        try:
            episode["controller"] = ours_agent.controller.diagnostics()
        except Exception:  # noqa: BLE001 - diagnostics must never cost an episode
            episode["controller"] = {}
    if planner.RECORD_TRAJECTORY and ours_agent is not None:
        # agent_for(seat), not a global: in a self-play episode both seats have an
        # Agent and the other one's journal is the opponent's, not ours.
        episode["trajectory"] = ours_agent.journal
    return episode


# --- aggregation --------------------------------------------------------------


def wilson(wins: float, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    d = 1 + z**2 / n
    c = p + z**2 / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return round((c - s) / d, 4), round((c + s) / d, 4)


def summarise(episodes: list[dict], split_by_opponent: bool = True) -> dict:
    """Aggregate episodes.

    With more than one opponent in a protocol the pooled mean blends two different
    games — beating `pass` by 900 and losing to `starter` by 100 averages to a
    number describing neither. `by_opponent` keeps the pooled figure (it is the
    honest "against the field" analogue of the leaderboard's Bradley-Terry fit)
    while making the components visible.
    """
    ok = [e for e in episodes if e["status"] == "DONE" and e["margin"] is not None]
    errors = len(episodes) - len(ok)
    if not ok:
        return {"n": 0, "errors": errors}

    margins = [e["margin"] for e in ok]
    scores = [e["ours"] for e in ok]
    wins = sum(1 for m in margins if m > 0)
    ties = sum(1 for m in margins if m == 0)
    lo, hi = wilson(wins, len(ok))
    slo, shi = wilson(wins + 0.5 * ties, len(ok))
    out = {
        "n": len(ok),
        "errors": errors,
        "wins": wins,
        "losses": len(ok) - wins - ties,
        "ties": ties,
        "win_rate": round(wins / len(ok), 4),
        "wilson_lo": lo,
        "wilson_hi": hi,
        # Ties are half a point, not a loss. Kaggle's rating updates on
        # win/loss/TIE, so `win_rate` understates any config that draws — most
        # visibly a deterministic strategy in a mirror match, which ties every
        # single game and scores 0% by the wins-only measure. Wilson on a
        # half-integer count is an approximation, but a far better one than
        # pretending 60 draws were 60 defeats.
        "score_rate": round((wins + 0.5 * ties) / len(ok), 4),
        "score_lo": slo,
        "score_hi": shi,
        "mean_margin": round(statistics.mean(margins), 1),
        "median_margin": round(statistics.median(margins), 1),
        "mean_score": round(statistics.mean(scores), 1),
        # Reported so a sweep can tell "config is better" from "seeds were kind".
        "stdev_margin": round(statistics.stdev(margins), 1) if len(margins) > 1 else 0.0,
    }
    # .get: aggregation must not depend on a key an episode dict might lack.
    opponents = sorted({e.get("opponent") for e in episodes} - {None})
    if split_by_opponent and len(opponents) > 1:
        out["by_opponent"] = {
            o: summarise([e for e in episodes if e.get("opponent") == o],
                         split_by_opponent=False)
            for o in opponents
        }
    return out


def aggregate_diagnostics(episodes: list[dict]) -> dict:
    """Sum per-episode controller counters across a run.

    Scalars are summed; equal-length lists are summed element-wise (that is how
    per-rule fire counts survive). `_episodes` records how many episodes actually
    reported, so a zero can be read as "never fired" rather than "never asked".

    A zero in `fires` is the signal worth watching: it means a rule is
    unreachable, the config is behaviourally its catch-all, and a sweep tuning
    that rule's threshold is searching a flat surface.
    """
    out: dict = {}
    n = 0
    for ep in episodes:
        diag = ep.get("controller")
        if not isinstance(diag, dict) or not diag:
            continue
        n += 1
        for k, v in diag.items():
            if isinstance(v, list):
                cur = out.get(k)
                if cur is None:
                    out[k] = list(v)
                elif len(cur) == len(v):
                    out[k] = [a + b for a, b in zip(cur, v)]
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = out.get(k, 0) + v
    if n:
        out["_episodes"] = n
    return out


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
    from agentlib.strategies import apply_params, build_all

    known = {s.name for s in build_all()}
    controller = build_controller(spec, known=known, strict=True)
    # Strategy `params`, validated HERE and thrown away. Inside an episode
    # `build_agent` applies them non-strictly, because a bad param must degrade
    # play rather than error a submission — which means a misspelt param name
    # would print a warning into a worker's stderr and run the defaults. Every
    # trial would then be identical and the flat response surface would read as a
    # finding about the game. One strict pass in the parent turns that into a
    # crash before any episode starts.
    apply_params(build_all(), spec.get("params"), strict=True)
    # Mirrors Agent.action_space — the index->strategy mapping a trained policy
    # will need, stored with the trajectories so it can't drift away from them.
    action_space = sorted(known)

    # Before the pool starts: a bad opponent name must not surface minutes later.
    for opp in proto["opponents"]:
        parse_opponent(opp)

    seats = [0, 1] if proto.get("swap_seats") else [0]
    jobs_list = [
        (seed, opp, seat, steps, spec)
        for seed in seeds
        for opp in proto["opponents"]
        for seat in seats
    ]

    t0 = time.time()
    workers = jobs or default_jobs()
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
        "controller_diagnostics": aggregate_diagnostics(episodes),
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

#: What a sweep may maximise. Anything here must be a key of `summarise()` or be
#: handled explicitly in `score()`.
#:
#: `score_lo` is the one to reach for once a protocol has a real opponent;
#: `wilson_lo` is its wins-only sibling and treats every draw as a defeat. Kaggle
#: ranks on win/loss/tie only — the coin margin is discarded — so `mean_margin`
#: optimises a quantity the leaderboard ignores. The lower bound rather than raw
#: `win_rate` because a lucky 10/10 should not outrank a solid 55/60, and because
#: a saturated 100% win rate has no gradient for BO to follow.
OBJECTIVES = ("mean_margin", "median_margin", "mean_score",
              "win_rate", "wilson_lo", "score_rate", "score_lo", "margin_z")

#: Floor on σ for `margin_z`: a config that never loses would otherwise divide by
#: ~0 and score unbounded, which BO will chase straight off a cliff.
MIN_STDEV = 1.0


def score(summary: dict, objective_name: str | None = None, opponent: str | None = None) -> float:
    """Reduce a summary to the single number a sweep maximises.

    `opponent` selects one component of a multi-opponent protocol — optimise
    "beats starter" rather than a blend dominated by whichever opponent is easier.
    """
    name = objective_name or OBJECTIVE
    if name not in OBJECTIVES:
        raise ValueError(f"unknown objective {name!r}; choose from {', '.join(OBJECTIVES)}")
    if opponent:
        summary = (summary.get("by_opponent") or {}).get(opponent) or {}
    if not summary.get("n"):
        return float("-inf")
    if name == "margin_z":
        return float(summary["mean_margin"]) / max(summary.get("stdev_margin", 0.0), MIN_STDEV)
    return float(summary[name])


def objective(spec: dict, objective_name: str | None = None,
              opponent: str | None = None, **kw) -> float:
    """Scalar objective for Optuna: `evaluate` reduced to one number.

    An all-errored config returns -inf rather than raising, so one bad proposal
    can't kill a study mid-sweep.

        study.optimize(lambda t: objective(spec_from(t), study=t.study.study_name,
                                           trial=t.number), n_trials=200)
    """
    return score(evaluate(spec=spec, **kw)["summary"], objective_name, opponent)


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--config", help="a configs/*.yaml file")
    src.add_argument("--strategy", help="measure one strategy in isolation (builds a fixed spec)")
    ap.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    ap.add_argument("--split", default="train", choices=["train", "holdout"])
    ap.add_argument("--jobs", type=int, default=None,
                    help=f"worker processes; default is usable cores-1 "
                         f"(={default_jobs()} here), or $KAGGRICULTURE_JOBS")
    ap.add_argument("--note", default="")
    ap.add_argument("--wandb", action="store_true",
                    help="mirror this result into Weights & Biases (or set KAGGRICULTURE_WANDB=1)")
    ap.add_argument("--trajectory", action="store_true",
                    help="record RL transitions (features/action/mask); forces 1 worker")
    ap.add_argument("--objective", default=OBJECTIVE, choices=OBJECTIVES,
                    help=f"scalar a sweep would maximise (default {OBJECTIVE})")
    ap.add_argument("--opponent", default=None,
                    help="score against ONE opponent of a multi-opponent protocol")
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
    workers = args.jobs or default_jobs()
    print(f"  jobs={workers}{'' if args.jobs else ' (auto)'}")
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
    for opp, sub in sorted((s.get("by_opponent") or {}).items()):
        print(f"    vs {opp:<10} n={sub['n']:>3} win={sub['win_rate']:>6.1%} "
              f"[{sub['wilson_lo']:.1%},{sub['wilson_hi']:.1%}]  "
              f"margin={sub['mean_margin']:+8.0f} sd={sub['stdev_margin']:>5.0f}")
    diag = rec.get("controller_diagnostics") or {}
    fires = diag.get("fires")
    if fires is not None:
        rules = (rec["controller"].get("rules") or rec["controller"].get("schedule") or [])
        parts = []
        for i, count in enumerate(fires):
            name = rules[i].get("strategy", "?") if i < len(rules) else "?"
            parts.append(f"{i}:{name}={count}")
        print(f"  rule fires: {'  '.join(parts)}")
        dead = [i for i, c in enumerate(fires) if c == 0]
        if dead:
            print(f"  !! rule(s) {dead} NEVER fired in {diag.get('_episodes', 0)} episodes — "
                  "unreachable, so this config is behaviourally its catch-all.")
            print("  !! Tuning their thresholds in a sweep searches a flat surface.")
    tgt = f" vs {args.opponent}" if args.opponent else ""
    print(f"  objective[{args.objective}{tgt}] = "
          f"{score(s, args.objective, args.opponent):+.3f}")
    print(f"  -> {RESULTS.relative_to(ROOT)}  ({rec['wall']}s)")
    if rec.get("wandb_url"):
        print(f"  -> {rec['wandb_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
