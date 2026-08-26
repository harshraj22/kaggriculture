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

Which config an episode runs is set by env var, because `main.py` is `exec`'d by the
env's loader rather than called with arguments. `--config` / `--controller` on
`run_match.py` and `evaluate.py` just set these:

```bash
KAGGRICULTURE_CONFIG=configs/threshold_demo.yaml   # which config
KAGGRICULTURE_CONTROLLER=priority                  # override its `type`
```

### Where things stand

| config | mean score | vs. `pass`, protocol v1/train |
|---|---|---|
| `safe_only` | 3889 | SafeFarmer all season |
| `threshold_demo` | 3889 | never crosses its `money_gte: 6000` threshold, so ≡ `safe_only` |
| `split_season` | 3623 | wheat_loop for 10 days, then SafeFarmer |
| `baseline` | 3197 | wheat_loop whenever eligible |

Starting money is 3000, so the best config nets **+889 over 30 days** — barely above
break-even. `wheat_loop` is currently *negative* value: it hires six hands a day and
plants ~15 seeds a game, losing to the stateless fallback by ~690. Fixing or deleting
it is the open item.

## Writing a controller

`select(obs, candidates)` gets the **full observation** — money, opponent money,
market prices and inventory, shed, tiles, day, hour. `ScheduleController` happens
to read only the turn number; nothing requires that. Controllers may also hold
per-episode state (one instance per episode; `reset()` clears it).

`controllers/threshold.py` is the reference. It reads live game state, carries
state, and cost exactly one file plus one registry line:

```yaml
type: threshold
rules:
  - { when: { day_gte: 25 },     strategy: safe_farmer }
  - { when: { money_gte: 6000 }, strategy: wheat_loop }
  - { when: {},                  strategy: safe_farmer }   # catch-all, required
```

That's the shape worth optimising: **the meaning of each condition is code, the
numbers are config.** BO then searches over `25` and `6000` — a continuous space
it handles far better than the categorical "which strategy in which day-slot".

Free once registered: eligibility masking, the `SafeFarmer` fallback, strict vs.
lenient loading, config hashing, `--controller <type>` override, and `describe()`
recorded into every result row.

## Layout

```
main.py                  submission entrypoint — must stay at archive root
agentlib/
  planner.py               the arbiter — observe all, pick one, act, notify all
  settings.py              config loading, env vars, strict/lenient
  game/                    knowledge of the game; decides nothing
    config.py                re-exports the env's own tables — nothing transcribed
    observation.py           Obs/Tile — defensive wrappers over the raw dict
    actions.py               TurnPlan builder + action validation
    market.py                sale planning on the env's price curve
  controllers/             who drives
    base.py                  the Controller interface
    priority.py schedule.py threshold.py rl.py (stub)
  strategies/              what to do
    base.py                  the Strategy interface
    desk.py                  MarketDesk — shared market logic, by composition
    safe_farmer.py           the default and universal fallback
    wheat_loop.py            stateful reference implementation
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
4. **The fallback is code, not config.** `SafeFarmer` and the failure policy live in
   `strategies/__init__.py` and `planner.py`. No config — including one an optimiser
   invented — can weaken the safety net.
5. **Measure with `tools/evaluate.py`,** never a single game. One game tells you
   nothing; the protocol runs 60 paired episodes in ~20s.
6. **Search on margin, decide on win rate.** The ladder scores win/loss only, so win
   rate is the true objective — but it's binary and noisy, and BO will burn episodes
   on it. Mean margin is lower-variance and correlated: optimise on margin, then
   validate the finalists on win rate against a real opponent.

## Where to start reading

[`notes/brainstorm.md`](notes/brainstorm.md) — strategy thinking and open questions.
[`results/experiments.jsonl`](results/experiments.jsonl) — every measurement taken so far.
