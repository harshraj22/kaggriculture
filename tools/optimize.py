#!/usr/bin/env python
"""Bayesian optimisation over controller configs.

    python tools/optimize.py --space split --trials 40
    python tools/optimize.py --space threshold --trials 200 --objective score_lo \
        --protocol eval/protocols/v3.yaml --opponent starter
    python tools/optimize.py --space split --trials 40 --study split-v3   # resumes

Each trial is one `evaluate()` call, so every trial lands in
`results/experiments.jsonl` with its `study`/`trial` recorded and the same three
provenance hashes as a hand-run config. A sweep is therefore not a separate
world: `compare.py` ranks its trials alongside everything else, and
`--vs <label>` gives paired deltas against a baseline.

## Adding a search space

Write a function `trial -> spec` and register it in `SPACES`. That is the whole
contract; nothing else in the file or the codebase changes. The spec you return
is the same dict a YAML config would have produced, so `bundle.py --activate`
can ship a winner verbatim.

## What is deliberately NOT searched

The fallback strategy, the strike policy and the eligibility rules. They are the
safety net; a config that could weaken them would let BO buy a better score by
disabling the thing that keeps a submission from erroring out.

## Two properties that make the numbers trustworthy

* **Seeds are fixed by the protocol.** Every trial plays the same worlds, so
  trials are paired and their differences carry far less noise than each trial's
  own standard deviation suggests.
* **Search on `train`, confirm on `holdout`.** `--split holdout` exists but the
  sweep should never use it; `best()` reports the winner so you can re-run it
  there once, at the end.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import load_env

load_env()

from evaluate import DEFAULT_PROTOCOL, OBJECTIVE, OBJECTIVES, evaluate, score

from agentlib.strategies import build_all

STUDIES = ROOT / "results" / "studies"

#: Strategy names available to a search space, in a stable order so a trial
#: number means the same thing on a resumed study.
STRATEGIES = sorted(s.name for s in build_all())

DAYS = 30
TURNS = 720


# --- search spaces ------------------------------------------------------------


def space_split(trial) -> dict:
    """Two-phase season: strategy A until day D, then strategy B.

    The obvious first sweep, and the one that shows why continuous parameters
    matter — `boundary` is a real number BO can reason about, where "which
    strategy in which of 30 day-slots" is a categorical space it cannot.
    """
    boundary = trial.suggest_int("boundary_day", 1, DAYS - 1)
    early = trial.suggest_categorical("early", STRATEGIES)
    late = trial.suggest_categorical("late", STRATEGIES)
    return {
        "type": "schedule",
        "schedule": [
            {"from_day": 0, "to_day": boundary - 1, "strategy": early},
            {"from_day": boundary, "to_day": DAYS - 1, "strategy": late},
        ],
    }


def space_threshold(trial) -> dict:
    """State-driven switching: a late-game rule, a wealth rule, a catch-all.

    The shape worth optimising — the *meaning* of each condition is code, the
    numbers are config. BO searches `25` and `6000`, not the rule structure.
    """
    return {
        "type": "threshold",
        "rules": [
            {
                "when": {"day_gte": trial.suggest_int("late_day", 15, DAYS - 1)},
                "strategy": trial.suggest_categorical("late", STRATEGIES),
            },
            {
                "when": {"money_gte": trial.suggest_int("rich_money", 3000, 20000, step=250)},
                "strategy": trial.suggest_categorical("rich", STRATEGIES),
            },
            # Catch-all is mandatory: without it, states matching no rule fall
            # through to the default and the config silently measures something
            # other than what it says.
            {"when": {}, "strategy": trial.suggest_categorical("base", STRATEGIES)},
        ],
    }


SPACES = {
    "split": space_split,
    "threshold": space_threshold,
}


# --- driver -------------------------------------------------------------------


def run(space="split", trials=20, protocol=DEFAULT_PROTOCOL, split="train",
        objective_name=OBJECTIVE, opponent=None, jobs=None, study_name=None,
        wandb=False, seed=0):
    try:
        import optuna
    except ImportError:
        print("[tools] optuna is not installed. It IS in requirements.txt — run:  make deps")
        return None

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    build = SPACES[space]
    study_name = study_name or f"{space}-{Path(protocol).stem}-{objective_name}"
    STUDIES.mkdir(parents=True, exist_ok=True)

    # SQLite storage so a study RESUMES. A 200-trial sweep is long enough that
    # losing it to a laptop sleeping is a real cost, and `load_if_exists` makes
    # re-running the same command continue rather than start over.
    study = optuna.create_study(
        direction="maximize",
        study_name=study_name,
        storage=f"sqlite:///{STUDIES / 'optuna.db'}",
        load_if_exists=True,
        sampler=optuna.samplers.TPESampler(seed=seed),
    )
    done = len(study.trials)
    print(f"study {study_name!r}: {done} trial(s) already done, running {trials} more")
    print(f"  space={space} objective={objective_name}"
          f"{f' vs {opponent}' if opponent else ''} protocol={Path(protocol).stem}/{split}")

    def objective(trial):
        spec = build(trial)
        rec = evaluate(
            spec=spec, protocol_path=protocol, split=split, jobs=jobs,
            note=f"{study_name}#{trial.number}", study=study_name, trial=trial.number,
            wandb=wandb,
        )
        value = score(rec["summary"], objective_name, opponent)
        s = rec["summary"]
        print(f"  #{trial.number:<4} {value:>9.3f}   "
              f"margin={s.get('mean_margin', float('nan')):+7.0f} "
              f"n={s.get('n', 0)}  {trial.params}")
        return value

    study.optimize(objective, n_trials=trials)
    return study


def report(study) -> None:
    print(f"\nbest of {len(study.trials)}:  {study.best_value:.4f}")
    for k, v in sorted(study.best_params.items()):
        print(f"  {k:<14} {v}")
    print("\nConfirm it on the holdout split before believing it:")
    print("  python tools/optimize.py ... --trials 0   # then re-run the winner "
          "with --split holdout")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", default="split", choices=sorted(SPACES))
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    ap.add_argument("--split", default="train", choices=["train", "holdout"])
    ap.add_argument("--objective", default=OBJECTIVE, choices=OBJECTIVES)
    ap.add_argument("--opponent", default=None,
                    help="optimise against ONE opponent of a multi-opponent protocol")
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--study", default=None, help="name; reuse it to resume")
    ap.add_argument("--seed", type=int, default=0, help="sampler seed, so a sweep replays")
    ap.add_argument("--wandb", action="store_true")
    args = ap.parse_args()

    if args.split == "holdout":
        print("!! Optimising on holdout destroys the only unbiased estimate you have.")

    study = run(args.space, args.trials, args.protocol, args.split, args.objective,
                args.opponent, args.jobs, args.study, args.wandb, args.seed)
    if study is None:
        return 1
    if study.trials:
        report(study)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
