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
make match OPP=random          # one local game
make arena OPP=starter N=20    # win rate over N games, with confidence interval
make test                      # unit tests (no env required, <1s)
make lint
make bundle                    # build + smoke-test submission.tar.gz
make submit MSG="wheat loop v1"
make status
make leaderboard
```

## Layout

```
main.py                  submission entrypoint — must stay at archive root
agentlib/                agent logic (see docs/DEPENDENCIES.md)
  config.py                re-exports the env's own tables — nothing transcribed
  market.py                sale planning on the env's curve — "what will 40 melons net?"
  observation.py           defensive wrappers over the raw obs dict
  actions.py               action builders + the Turn accumulator
  planner.py               entrypoint, never-raise guard
  strategies/              swappable strategies; wheat_loop is the baseline
tools/                   run_match.py · arena.py · bundle.py
tests/                   fast unit tests
docs/                    GAME_SPEC.md · COMPETITION.md · DEPENDENCIES.md
notes/                   brainstorm.md
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
