# Kaggriculture

Agent for the Kaggle [Kaggriculture](https://www.kaggle.com/competitions/kaggriculture)
simulation competition — a 30-day, 720-turn two-player farming game where the winner is
whoever banks the most coins.

## Setup

```bash
./setup.sh
source .venv/bin/activate
```

Then set up Kaggle auth (see [docs/COMPETITION.md](docs/COMPETITION.md)) and click
**Join Competition** on the website.

## Workflow

```bash
make test                                    # unit tests, <1s
make match OPP=starter                       # one local game, human-readable
make eval CONFIG=configs/safe_only.yaml      # score a config against protocol v1
make eval-all                                # score every config in configs/
make compare                                 # tabulate results/experiments.jsonl
make bundle && make submit MSG="v1"
```

## How an idea gets evaluated

Every experiment is `config × protocol → result record`, appended to
`results/experiments.jsonl`. A result is only comparable to another if the
**protocol** matches (same seeds, opponents, episode count) and the **code_hash**
matches — `wheat_loop` today is not `wheat_loop` next week. `compare.py` refuses
to rank across protocols and flags mixed code versions.

- `eval/protocols/v1.yaml` — the measurement contract. Editing it invalidates
  every prior result, so change it by adding `v2` instead.
- `configs/*.yaml` — controller specs; one file is one candidate agent.
- Seeds are fixed and shared across configs (paired comparison), so two configs
  play identical worlds and the difference between them isn't luck. `train` is
  for optimising, `holdout` is never optimised against.

Adding a strategy: write the class, register it in `strategies/__init__.py`.
Adding a controller: write the class, register it in `controllers/__init__.py`.
Adding an experiment: write a YAML file. Nothing else changes in any case.

## Layout

```
main.py                  submission entrypoint — must stay at archive root
agentlib/
  config.py                re-exports the env's own tables — nothing transcribed
  market.py                sale planning on the env's curve
  observation.py           defensive wrappers over the raw obs dict
  actions.py               TurnPlan builder + action validation
  strategy.py              the Strategy interface
  controller.py            the Controller interface
  controllers/             priority · schedule (config-driven) · rl (stub)
  strategies/              safe_farmer (default/fallback) · wheat_loop
  desk.py                  MarketDesk — shared market logic, by composition
  settings.py              config loading, env-var resolution, strict/lenient
  planner.py               the arbiter + never-raise guard
configs/                 controller specs — one file per candidate agent
eval/protocols/          versioned measurement contracts
results/                 experiments.jsonl, append-only
tools/                   evaluate.py · compare.py · run_match.py · arena.py · bundle.py
tests/                   fast unit tests
docs/ · notes/
```

## Design rules

1. **Never transcribe a number from the docs.** `kaggriculture.py` defines every table
   at module level; `config.py` re-exports them. If you're typing a constant, stop.
2. **Never raise.** `planner.decide` catches everything and falls back to `PASS`.
   A crashed episode is a guaranteed loss.
3. **`agentlib/` imports stdlib + `kaggle_environments` only** — nothing else.
4. **Optimize win rate, not profit.** Ratings move on win/loss only — margin is ignored.
5. **Measure with `arena.py`,** not single games. A 20-game sample still has a ±20% CI.

## Where to start reading

[`notes/brainstorm.md`](notes/brainstorm.md) — the strategy thinking and open questions.
