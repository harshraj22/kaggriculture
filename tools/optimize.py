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

from evaluate import (
    DEFAULT_PROTOCOL,
    OBJECTIVE,
    OBJECTIVES,
    default_jobs,
    evaluate,
    score,
)

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
                # LOG scale, not linear. Reachable money depends entirely on the
                # strategy: safe_farmer never exceeds ~3940, while a strong farm
                # ends near 15000. Sampled linearly over this range, ~80% of
                # trials would propose a threshold no weak strategy can cross —
                # every one of them behaviourally identical to the catch-all, and
                # BO burning its budget mapping a flat surface. Log scale puts the
                # density where the decision boundary actually is.
                "when": {"money_gte": trial.suggest_float("rich_money", 3000, 20000, log=True)},
                "strategy": trial.suggest_categorical("rich", STRATEGIES),
            },
            # Catch-all is mandatory: without it, states matching no rule fall
            # through to the default and the config silently measures something
            # other than what it says.
            {"when": {}, "strategy": trial.suggest_categorical("base", STRATEGIES)},
        ],
    }


def space_market_farm(trial) -> dict:
    """`market_farm`'s own constants, via the config's `params` block.

    The first space that searches inside a STRATEGY rather than over which
    strategy to run — see `agentlib/strategies/__init__.py::apply_params`. Every
    constant here is a starting point picked while reading the engine, not a
    measured optimum, and the docstring of `market_farm.py` says which reasoning
    produced each one.

    Ranges, and why they are these ranges:

    * `SATURATION` — 1.0 means "produce exactly what the town drinks". Under 1
      leaves room for the opponent's supply on a shared drain; over 1 pushes
      deliberately into the falling part of the curve. Both directions are
      arguable, so the range straddles 1.0.
    * `SELL_FLOOR_RATIO` — as a fraction of BASE. Under ~0.4 the throttle stops
      throttling; over ~0.95 nothing ever sells.
    * `CAPITAL_WEIGHT` / `CAPITAL_EASE` — the cash-scarcity blend that decides
      whether wheat or strawberry leads the opening. 0 recovers the pure
      coins-per-tile-day ranking, which is a MEASURED bankruptcy, so a search
      that lands near 0 is telling you something is wrong elsewhere.
    * `MAX_QUADRANTS` / `MAX_HANDS` / `MAX_PLANTS_PER_UNIT` — inherited from
      `wheat_farm`, where 2 / 8 / 4.0 were measured against a WHEAT rotation.
      A portfolio holding ongoing crops replants far less often and so has a
      different travel profile; whether the third quadrant is still a trap is an
      open question and this is how to answer it.
    """
    return {
        "type": "fixed",
        "strategy": "market_farm",
        "params": {
            "market_farm": {
                "SATURATION": trial.suggest_float("saturation", 0.5, 2.5),
                "SELL_FLOOR_RATIO": trial.suggest_float("sell_floor", 0.4, 0.95),
                "SHED_PRESSURE": trial.suggest_float("shed_pressure", 0.4, 0.95),
                "CAPITAL_WEIGHT": trial.suggest_float("capital_weight", 0.0, 1.5),
                "CAPITAL_EASE": trial.suggest_float("capital_ease", 2000, 40000,
                                                    log=True),
                "LEAD_SLACK_DAYS": trial.suggest_int("lead_slack", 0, 6),
                "MAX_QUADRANTS": trial.suggest_int("quadrants", 1, 4),
                "MAX_HANDS": trial.suggest_int("max_hands", 4, 12),
                "HANDS_PER_TILE": trial.suggest_float("hands_per_tile", 0.08, 0.40),
                "MAX_PLANTS_PER_UNIT": trial.suggest_float("plants_per_unit", 2.0, 8.0),
                "CASH_RESERVE": trial.suggest_float("cash_reserve", 100, 2000, log=True),
            },
        },
    }


def space_ranch_farm(trial) -> dict:
    """`ranch_farm`'s constants. Same `params` channel as `space_market_farm`.

    The two knobs that dominated every manual probe are `MAX_HANDS` and
    `HANDS_PER_ANIMAL`: 0.45/8 scored 4,662 and 0.80/14 scored 23,330 on an
    otherwise identical herd, because an animal that misses two meals escapes and
    a crew sized to the average day loses the herd on the bad ones. Both ranges
    therefore reach well past the measured point — the ceiling has not been found.

    `HERD_MIX` is categorical over the sensible subsets rather than free, because
    the ordering inside it is decided at runtime by coins-per-unit-turn and only
    membership is a real choice. GOOSE is in the space despite losing badly by
    default: it earns 33 coins/unit-turn against a sheep's 114, and a search that
    picks it anyway would be telling us something we do not currently know.
    """
    mixes = {
        "sheep": ["SHEEP"],
        "sheep_cow": ["SHEEP", "COW"],
        "sheep_cow_goose": ["SHEEP", "COW", "GOOSE"],
        "cow": ["COW"],
    }
    return {
        "type": "fixed",
        "strategy": "ranch_farm",
        "params": {
            "ranch_farm": {
                "HERD_MIX": mixes[trial.suggest_categorical("mix", sorted(mixes))],
                "HERD_SATURATION": trial.suggest_float("herd_saturation", 0.6, 1.8),
                "SELL_FLOOR_RATIO": trial.suggest_float("sell_floor", 0.4, 0.95),
                "SHED_PRESSURE": trial.suggest_float("shed_pressure", 0.4, 0.95),
                "FEED_DAYS_BUFFER": trial.suggest_int("feed_days", 1, 6),
                "FEED_SHED_SHARE": trial.suggest_float("feed_shed_share", 0.2, 0.8),
                "FEED_CARRY": trial.suggest_int("feed_carry", 4, 30),
                "MAX_QUADRANTS": trial.suggest_int("quadrants", 1, 3),
                "MAX_HANDS": trial.suggest_int("max_hands", 6, 20),
                "HANDS_PER_ANIMAL": trial.suggest_float("hands_per_animal", 0.3, 1.5),
                "ANIMALS_PER_UNIT": trial.suggest_float("animals_per_unit", 1.5, 6.0),
                "CASH_RESERVE_FOR_HERD": trial.suggest_float("herd_reserve", 200, 3000,
                                                             log=True),
                "HERD_LEAD_SLACK": trial.suggest_int("lead_slack", 0, 8),
            },
        },
    }


SPACES = {
    "split": space_split,
    "threshold": space_threshold,
    "market_farm": space_market_farm,
    "ranch_farm": space_ranch_farm,
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

        # Surface inert trials in the study itself. A rule that never fires makes
        # the trial a duplicate of its catch-all under a different name, which is
        # invisible in the objective value alone.
        fires = (rec.get("controller_diagnostics") or {}).get("fires")
        if fires is not None:
            trial.set_user_attr("fires", fires)
            dead = [i for i, c in enumerate(fires) if c == 0]
            if dead:
                trial.set_user_attr("inert_rules", dead)
        dead = trial.user_attrs.get("inert_rules")
        print(f"  #{trial.number:<4} {value:>9.3f}   "
              f"margin={s.get('mean_margin', float('nan')):+7.0f} "
              f"n={s.get('n', 0)}  {trial.params}"
              f"{f'   [inert rules {dead}]' if dead else ''}")
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
    ap.add_argument("--jobs", type=int, default=None,
                    help=f"worker processes per trial; default is usable cores-1 "
                         f"(={default_jobs()} here), or $KAGGRICULTURE_JOBS")
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
