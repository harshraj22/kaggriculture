"""Weights & Biases tracking for experiment records.

**tools/ only.** `agentlib/` must never import wandb — see `tools/_env.py` for
why the submission boundary is enforced rather than trusted.

Opt-in: nothing here runs unless `--wandb` is passed or `KAGGRICULTURE_WANDB=1`
is set. A local `make eval` shouldn't need network access or an API key.

`results/experiments.jsonl` stays the source of truth. W&B is a view over it —
which is why `tools/sync_wandb.py` can rebuild the whole project from the file,
and why losing your W&B account costs you nothing.

## The thing to be careful about

`compare.py` refuses to rank across differing `protocol_hash` and shouts about
mixed `code_hash`, because a score is meaningless without knowing what measured it
and what was measured. **W&B has no such guard** — the UI will cheerfully put two
runs on the same axis regardless. So both hashes are logged as config fields *and*
as tags, and runs are grouped by `protocol/split`. Filter on `code_hash` before
concluding a config helped; otherwise you'll credit a config for a strategy fix
that happened underneath it.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ENV_ENABLE = "KAGGRICULTURE_WANDB"
DEFAULT_PROJECT = "kaggriculture"


#: Consecutive failures before this process stops trying.
#:
#: An unreachable W&B costs a ~15s `WANDB_INIT_TIMEOUT` per call. That's a shrug
#: for one `make eval` and 50 minutes of dead waiting across a 200-trial Optuna
#: sweep. Tracking is observability: it should degrade to nothing, not become the
#: slowest part of the run.
MAX_CONSECUTIVE_FAILURES = 3
_failures = 0


def enabled(flag: bool = False) -> bool:
    if _failures >= MAX_CONSECUTIVE_FAILURES:
        return False
    return bool(flag or os.environ.get(ENV_ENABLE))


def reset_failures() -> None:
    """Re-arm after a transient outage. Mostly for tests."""
    global _failures
    _failures = 0


def _flatten_controller(desc: dict) -> dict:
    """Expand controller params into flat config keys.

    Parallel-coordinate plots need scalars, not nested dicts — this is what makes
    an Optuna sweep legible: `boundary_0` vs `mean_margin` across 200 trials.
    """
    out = {"controller": desc.get("type")}
    kind = desc.get("type")

    if kind == "fixed":
        out["strategy"] = desc.get("strategy")

    elif kind == "schedule":
        rules = desc.get("schedule", [])
        out["n_rules"] = len(rules)
        for i, r in enumerate(rules):
            out[f"rule{i}_strategy"] = r.get("strategy")
            out[f"rule{i}_from_turn"] = r.get("from_turn")
            out[f"rule{i}_to_turn"] = r.get("to_turn")
        # The boundaries are what BO actually tunes.
        for i, r in enumerate(rules[:-1]):
            out[f"boundary_{i}"] = r.get("to_turn")

    elif kind == "threshold":
        rules = desc.get("rules", [])
        out["n_rules"] = len(rules)
        out["switches"] = desc.get("switches")
        for i, r in enumerate(rules):
            out[f"rule{i}_strategy"] = r.get("strategy")
            for cond, value in (r.get("when") or {}).items():
                out[f"rule{i}_{cond}"] = value

    elif kind == "priority":
        out["order"] = ",".join(desc.get("order", []))

    elif kind == "rl":
        out["policy"] = desc.get("policy")

    return out


def _run_name(record: dict) -> str:
    ctrl = record.get("controller") or {}
    if record.get("trial") is not None:
        return f"{record.get('study', 'trial')}#{record['trial']}"
    if ctrl.get("type") == "fixed":
        return f"[{ctrl['strategy']}]"
    if record.get("config_path"):
        return Path(record["config_path"]).stem
    return f"{ctrl.get('type', 'spec')}:{record['config_hash'][:6]}"


def log_record(record: dict, project=None, flag: bool = False) -> str | None:
    """Mirror one experiment record into W&B.

    Returns the run URL on success, `""` on success without a URL (offline mode
    has no URL — that is not a failure), and **None only when logging failed** so
    callers can distinguish the two. Conflating them makes `sync_wandb.py` abort
    on its first offline run.

    Never raises: tracking is observability, and a network problem must not lose
    a measurement that took 20 seconds of compute and is already on disk.
    """
    global _failures

    if not enabled(flag):
        return None
    try:
        import wandb
    except ImportError:
        print("[tools] --wandb requested but wandb is not installed.")
        print("[tools] It IS in requirements.txt — your venv is just stale. Run:  make deps")
        return None

    try:
        summary = record.get("summary") or {}
        config = {
            **_flatten_controller(record.get("controller") or {}),
            "config_hash": record["config_hash"],
            "config_path": record.get("config_path"),
            "protocol_id": record["protocol_id"],
            "protocol_hash": record["protocol_hash"],
            "split": record.get("split"),
            "code_hash": record["code_hash"],
            "git_rev": record.get("git_rev"),
            "git_dirty": record.get("git_dirty"),
            "study": record.get("study"),
            "trial": record.get("trial"),
        }

        run = wandb.init(
            project=project or os.environ.get("WANDB_PROJECT", DEFAULT_PROJECT),
            name=_run_name(record),
            id=record["run_id"],          # idempotent: re-syncing won't duplicate
            group=f"{record['protocol_id']}/{record.get('split', 'train')}",
            job_type=(record.get("controller") or {}).get("type", "unknown"),
            tags=[
                f"protocol:{record['protocol_id']}",
                f"code:{record['code_hash']}",
                f"split:{record.get('split', 'train')}",
                *(["dirty"] if record.get("git_dirty") else []),
                *([f"study:{record['study']}"] if record.get("study") else []),
            ],
            config=config,
            notes=record.get("note") or "",
            resume="allow",
            reinit=True,
        )

        run.summary.update(summary)

        episodes = record.get("episodes") or []
        if episodes:
            table = wandb.Table(
                columns=["seed", "opponent", "seat", "ours", "theirs", "margin", "status"],
                data=[
                    [e.get("seed"), e.get("opponent"), e.get("seat"), e.get("ours"),
                     e.get("theirs"), e.get("margin"), e.get("status")]
                    for e in episodes
                ],
            )
            # Per-seed detail: with paired seeds, a config that loses on exactly
            # one seed is a different problem from one that's uniformly worse.
            run.log({"episodes": table})

        # `or ""`: offline runs have no URL, which is success, not failure.
        url = run.url or ""
        run.finish()
        _failures = 0
        return url
    except Exception as e:  # noqa: BLE001
        _failures += 1
        print(f"[tools] wandb logging failed ({type(e).__name__}: {e}); the result is "
              "still in results/experiments.jsonl")
        if _failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"[tools] {_failures} consecutive failures — disabling wandb for this "
                  "process so a dead endpoint doesn't stall the run.")
        return None
