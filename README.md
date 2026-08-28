# Kaggriculture

Agent for the Kaggle [Kaggriculture](https://www.kaggle.com/competitions/kaggriculture)
simulation competition — a 30-day, 720-turn two-player farming game where the winner is
whoever banks the most coins.

## Setup

```bash
./setup.sh                 # first time
source .venv/bin/activate

make deps                  # after a git pull, if requirements.txt changed
```

If a tool reports a package "is not installed", check `requirements.txt` before
reaching for `pip install` — it's almost certainly listed and your venv is just
behind. `make deps` fixes it.

Then set up Kaggle auth (see [docs/COMPETITION.md](docs/COMPETITION.md)) and click
**Join Competition** on the website.

## Workflow

```bash
make test                                    # unit tests, <1s
make match OPP=starter                       # one local game, human-readable
make eval-strategy S=wheat_loop              # score ONE strategy in isolation
make eval CONFIG=configs/safe_only.yaml      # score a controller config
make eval-all                                # every config AND every strategy
make sweep SPACE=split TRIALS=40             # Bayesian search over configs
make compare VS=safe_only                    # tabulate, with paired deltas

# against real opponents, optimising what the leaderboard actually rewards:
make eval CONFIG=configs/safe_only.yaml PROTOCOL=eval/protocols/v3.yaml OBJ=score_lo
make sweep SPACE=threshold TRIALS=200 PROTOCOL=eval/protocols/v3.yaml OBJ=score_lo
make activate CONFIG=configs/safe_only.yaml  # choose what a SUBMISSION runs
make submit MSG="v1"
```

**`make activate` is not optional.** Kaggle's runner sets no environment
variables, so `configs/active.yaml` is the only channel by which a chosen config
reaches a submitted agent. Without it the agent falls back to the builtin
controller and everything the optimiser found is discarded. `bundle.py` warns
loudly if the file is missing.

## How an idea gets evaluated

Every experiment is `config × protocol → result record`, appended to
`results/experiments.jsonl`. A result is only comparable to another if the
**protocol** matches (same seeds, opponents, episode count) and the **code_hash**
matches — `wheat_loop` today is not `wheat_loop` next week. `compare.py` refuses
to rank across protocols and flags mixed code versions.

- `eval/protocols/*.yaml` — the measurement contract. Editing one invalidates every
  prior result, so changes go in a **new** file with a new `id`.
  - **v1** — 30 seeds × 2 seats = 60 episodes, ~20s. Fast; use it to search.
  - **v2** — 140 seeds × 2 seats = 280 episodes, ~6 min. Sized from measurement:
    1.32.6's shops-with-replacement more than doubled per-game variance
    (same strategy, same seeds: sd 32 → 68), so v1's precision needs ~271 episodes
    to recover. Use it to confirm a sweep's finalists.
  - **v3** — v1's worlds, but three opponents: `pass`, `starter` and `self`
    (180 episodes). The only protocol on which the leaderboard's own metric
    means anything, because against `pass` every config wins 100% of the time
    and `win_rate` has no gradient. Use it with `--objective score_lo`.
- `configs/*.yaml` — controller specs; one file is one candidate agent.
  `configs/active.yaml` is the one a submission runs (`make activate`);
  its `.json` twin is generated beside it and is all that ships.
- Seeds are fixed and shared across configs (paired comparison), so two configs
  play identical worlds and the difference between them isn't luck. `train` is
  for optimising, `holdout` is never optimised against.
- `results/` is gitignored — it's machine-local and regenerable via `make eval-all`.
  Use `make wandb` to share numbers with anyone else.

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

For local defaults, `cp .env.example .env` and edit — `tools/` load it, and a real
environment variable still overrides the file. **`agentlib/` never reads a `.env`**:
the agent runs in Kaggle's sandbox where neither the file nor python-dotenv exists,
and a config channel that behaves differently locally than in a submission is how
you ship something you never tested. A test enforces this, alongside one that
rejects any unguarded third-party import under `agentlib/`.

### Where things stand

Protocol **v3**/train, 180 episodes, kaggle-environments 1.32.7. `score_lo` is the
Wilson lower bound on (wins + 1/2 ties)/n — the leaderboard's metric, since Kaggle
rates on win/loss/tie and discards the margin.

| config | score_lo | vs `pass` | vs `starter` | mirror |
|---|---|---|---|---|
| **`wheat_farm`** | **0.772** | **+12873** (100%) | **+12336** (100%) | 43% |
| `baseline` / `[wheat_loop]` | 0.772 | +991 (100%) | +459 (100%) | 8% |
| `safe_only` / `[safe_farmer]` | 0.748 | +851 (100%) | +232 (93%) | tie |
| `split_season` | 0.742 | +768 (100%) | +226 (92%) | tie |

`wheat_farm` scores **15873** train / **15904** holdout on v1 — past the ~15,400
a competitor reported publicly, and roughly 4.5x the built-in `starter`. Its
docstring is the specification: engine rules, per-turn algorithm, tuned constants,
and the variants that were measured and rejected.

Note the mirror column: `wheat_farm` is the first strategy that does *not* draw
itself. Two copies contend for the same market and one wins by a real margin
(sd 824 on a zero mean), which is the market coupling finally showing up. It also
means `score_lo` no longer separates it from `wheat_loop` — both sit at 0.772,
because pooled win rate saturates once you beat `pass` and `starter` every game.
**The next protocol needs a harder opponent than `starter`**, or the objective
stops discriminating exactly where we now need it to.

### Reading the error bars

`make compare` shows a **marginal** `sd` — the spread of one config's own results
across seeds. That is the right number for "how good is this agent, absolutely",
and it is large: 68 coins on 1.32.7, up from 32 on 1.32.2 because shops are now
drawn with replacement and some worlds are simply richer.

It is the **wrong** number for "is A better than B". Every config plays the same
seeds, so that world-to-world swing is common to both and cancels:

```bash
make compare VS=safe_only      # or: python tools/compare.py --vs safe_only
```

```
config                  n    delta    sd_p    se_p          95% CI    rho   se if unpaired
split_season           60     -271    30.7     4.0    [-278, -263]   0.89              11.7
baseline               60     -700    49.2     6.4    [-712, -687]   0.81              13.9
```

`rho` around 0.8–0.9 is why the paired error bar is ~2–3× tighter. Judge rankings
on `se_p`. Two consequences worth keeping in mind:

- A sweep on v1 discriminates far better than the marginal `sd` suggests, because
  Optuna's trials all share seeds and so are implicitly paired.
- `rho` is high *because* our current strategies all ignore the shop roll. A
  melon or shop-arbitrage strategy will correlate less with the baseline, `sd_p`
  will rise toward the unpaired figure, and the larger protocol starts to earn
  its cost. Watch the `rho` column as strategies diverge.

Upgrading the env is deliberate: `make deps` installs without upgrading,
`make deps-upgrade` upgrades and warns you that baselines need re-running.

## Running a sweep

```bash
make sweep SPACE=split TRIALS=40                      # v1, mean_margin
make sweep SPACE=threshold TRIALS=200 \
     PROTOCOL=eval/protocols/v3.yaml OBJ=score_lo     # contested, leaderboard metric
```

A search space is a function `trial -> spec` registered in `SPACES` in
`tools/optimize.py`. That is the whole contract — the spec it returns is the same
dict a YAML config would have produced, so `make activate` can ship a winner
verbatim, and every trial lands in `results/experiments.jsonl` with its
`study`/`trial` and the usual three hashes. `compare.py` ranks sweep trials
alongside hand-run configs; there is no separate sweep world.

Studies are SQLite-backed and **resume**: re-running the same `--study` name
continues rather than restarting, which matters once a sweep outlives a laptop
battery. What is deliberately *not* searchable: the fallback strategy, the strike
policy, the eligibility rules. BO would otherwise buy a better score by
disabling the safety net.

Search on `train`, confirm the winner once on `holdout`.

## Playing both seats

`KAGGRICULTURE_CONFIG_0` / `_1` give each seat its own config:

```bash
KAGGRICULTURE_CONFIG_0=configs/safe_only.yaml \
KAGGRICULTURE_CONFIG_1=configs/baseline.yaml python tools/run_match.py --opponent self
```

This needs a seat-indexed variable rather than a plain one because
`kaggle_environments` runs **both players in a single interpreter** — `agentlib`
is cached in `sys.modules`, so one process-wide variable cannot say two different
things. For the same reason `planner` keeps **one Agent per seat**, not a module
singleton: with a singleton the two seats share strikes, journal and every
stateful strategy's internals. In a symmetric mirror that happens to be
invisible; the moment the seats run different configs it is not.

The variables are absent in a submission, where `configs/active.json` is the only
channel that matters.

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

## Experiment tracking (Weights & Biases)

Opt-in — `--wandb`, or `KAGGRICULTURE_WANDB=1` in `.env`:

```bash
python tools/evaluate.py --strategy wheat_loop --wandb
make wandb          # backfill every past result from results/experiments.jsonl
```

`results/` is **gitignored**, so W&B is the *shared* record and the JSONL is a
local cache. A fresh clone has no results and `make compare` will be empty until
you run something — push anything a collaborator should see with `make wandb`.

Runs are created with `id=run_id`, so `make wandb` is idempotent: delete the
project, re-run it, and you're whole again from your local JSONL.

**Online vs offline.** The code path is identical either way — `wandb.init()`
handles it, we never branch on mode. With `WANDB_MODE` unset (the default) and an
API key, runs push to the cloud as they finish and the run URL is printed and
stored in the record. With `WANDB_MODE=offline`, real `.wandb` files are written
under `wandb/offline-run-*` and `make wandb-push` uploads them later — verified
with `wandb sync --show`, which lists them as syncable.

**Failures are contained, then throttled.** A tracking outage never costs a
measurement: the record is written to disk *before* the W&B call, and a failure
prints and moves on. After 3 consecutive failures tracking disables itself for
the process — an unreachable endpoint costs ~15s per call, which is a shrug for
one `make eval` and 50 minutes across a 200-trial sweep.

What's logged, and why it's shaped this way:

- **Controller params are flattened** into config keys (`strategy`, `boundary_0`,
  `rule0_money_gte`, …). Parallel-coordinate plots need scalars, and that's what
  makes a 200-trial Optuna sweep legible: boundary vs. `mean_margin` at a glance.
- **`group = protocol/split`** so the UI's default comparison is between things
  that are actually comparable.
- **`job_type = controller type`**, so "all `fixed` runs" is one click — that's the
  per-strategy comparison.
- **An `episodes` table** per run. With paired seeds, a config that loses on one
  seed is a different problem from one that's uniformly worse, and only per-seed
  data distinguishes them.

⚠️ **W&B has no comparability guard.** `compare.py` refuses to rank across
differing `protocol_hash` and shouts about mixed `code_hash`; the W&B UI will
happily put any two runs on the same axis. Both hashes are logged as config
fields *and* as tags — **filter on `code_hash` before concluding a config helped**,
or you'll credit a config for a strategy fix that landed underneath it. That
failure already happened once in this repo's history, which is why `compare.py`
exists at all.

## Hooking up Optuna

`evaluate()` takes an in-memory spec, so a trial's config never touches disk:

```python
from tools.evaluate import objective
from agentlib.controllers.fixed import spec_for   # or build any spec dict

def run(trial):
    spec = {"type": "schedule", "schedule": [
        {"from_day": 0, "to_day": trial.suggest_int("split", 1, 28), "strategy": "wheat_loop"},
        {"from_day": trial.suggest_int("split", 1, 28) + 1, "to_day": 29, "strategy": "safe_farmer"},
    ]}
    return objective(spec, study=trial.study.study_name, trial=trial.number)
```

Three things to get right:

- **Don't use Optuna's pruners at first.** Pruning after a few seeds breaks the
  paired comparison that keeps our variance small. Prune only on whole-seed-set
  boundaries, if at all.
- **Don't nest parallelism.** `evaluate` already parallelises episodes; run the
  study sequentially or the process pools fight.
- **Two stores, deliberately.** Optuna's storage owns search state; ours owns
  provenance (`code_hash`, `protocol_hash`). `study` and `trial` are recorded in
  every row so they can be joined.

## Training an RL controller

`--trajectory` records, per turn, the feature vector, the chosen **action index**,
and the **eligibility mask** — the mask being the part that can't be reconstructed
later, and without which offline log-probabilities are wrong.

```bash
python tools/evaluate.py --strategy safe_farmer --trajectory
# -> results/trajectories/<run_id>.jsonl, referenced from the result row
```

Off by default, so a submission pays nothing for training plumbing. Trajectories
are ~130 KB per episode and live in their own file; inlining them would grow the
results store by ~8 MB per experiment.

Three things that will bite:

- **Credit assignment.** 720 decisions, one terminal reward. Money-delta-per-step
  is tempting but spikes on sales, so it rewards liquidating over growing.
- **The action space is currently size 2**, one of which is broken. RL over
  "`safe_farmer` or `wheat_loop`" can't learn anything — this is blocked on having
  several genuinely different, working strategies.
- **Inference has to ship.** Torch won't be in the submission sandbox. Train with
  torch, export weights to `.npz`, infer with numpy — which is why
  `controllers/rl.py` loads an artifact rather than a model object.

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
    fixed.py priority.py schedule.py threshold.py rl.py (stub)
  strategies/              what to do
    base.py                  the Strategy interface
    desk.py                  MarketDesk — shared market logic, by composition
    safe_farmer.py           the default and universal fallback
    wheat_loop.py            stateful reference implementation
configs/                 controller specs (YAML); active.yaml = what a SUBMISSION runs
eval/protocols/          versioned measurement contracts
results/                 experiments.jsonl (append-only) · trajectories/ (RL)
tools/                   evaluate.py · compare.py · run_match.py · bundle.py
                         tracking.py · sync_wandb.py · _env.py  (tools-only deps)
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
   nothing; the protocol runs 60 paired episodes in ~20s. (`arena.py` was deleted:
   it never reset the agent between games, so every run after the first was
   contaminated with the previous game's state.)
6. **Search on margin, decide on win rate.** The ladder scores win/loss only, so win
   rate is the true objective — but it's binary and noisy, and BO will burn episodes
   on it. Mean margin is lower-variance and correlated: optimise on margin, then
   validate the finalists on win rate against a real opponent.

## Where to start reading

[`notes/brainstorm.md`](notes/brainstorm.md) — strategy thinking and open questions.
[`results/experiments.jsonl`](results/experiments.jsonl) — every measurement taken so far.
