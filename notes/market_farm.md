# market_farm — handoff

Everything measured while building the drain-sized portfolio strategy, and the
exact commands to continue on a bigger machine. Written to be readable without
this session's context.

**Environment for every number here:** kaggle-environments 1.32.7, no protocol
(direct `env.run` over seeds 1..12, both seats, so 24 paired episodes), Linux, 3
worker processes. Wall clock ~4.4 s/episode/core, so ~2,600 episodes/hour at 3
jobs on a 4-core box. Nothing here has been through `tools/evaluate.py`, so none
of it is in `results/experiments.jsonl` — **re-run the baselines through a
protocol before comparing them to anything that is.**

## 1. Why the strategy exists

`melon_farm` — the current submission — against the committed public agent:

```
ours   31,935        v48   156,747        0 W / 0 T / 24 L
margin -124,812   sd 29,410   best case -50,533
```

Sell logs from one episode explain the ~5×:

```
ours    34 SELL actions:  WHEAT 668, MELON 120
v48    572 SELL actions:  STRAWBERRY 430, FERTILIZER 397, MILK 335,
                          WHEAT 313, WOOL 179, MELON 72, CARROT 57
```

Market at the final bell:

```
MELON      10,156  ->    7   (base 250)
MILK        9,874  ->  258   (base 160)
STRAWBERRY  9,859  ->  220   (base 120)
TOMATO      9,754  ->  100   (base  60)
```

Every good with shop demand ended **under**-supplied and above base. The one good
we concentrated on ended flooded at 3% of base. The gap is price, not production.

## 2. The economic table this all rests on

Measured over 5 full episodes with **both seats passing**, so it is the town's
appetite alone with no player supply:

| good | base | drain/season | season value at base | above_func |
|---|---:|---:|---:|---|
| WOOL | 200 | 368 | **73,680** | sq/3.2 |
| STRAWBERRY | 120 | 448 | **53,712** | linear/1.6 |
| MILK | 160 | 278 | **44,544** | linear/1.6 |
| WHEAT | 25 | 556 | 13,890 | log/0.2 |
| TOMATO | 60 | 217 | 13,032 | sqrt/0.6 |
| EGG | 50 | 239 | 11,940 | log/0.2 |
| CARROT | 35 | 235 | 8,232 | sqrt/0.7 |
| MELON | 250 | 30 | 7,500 | sq/3.6 |
| FERTILIZER | 100 | 0 | 0 | linear/0.4 |
| **TOTAL** | | | **226,530** | |

Reproduce with `agentlib.game.market.drain_per_day`, or empirically:

```bash
.venv/bin/python -c "
from kaggle_environments import make
from kaggle_environments.envs.kaggriculture import kaggriculture as e
env = make('kaggriculture', configuration={'seed': 1})
env.run([lambda o: {'farmer':['PASS'],'hands':[],'market':[]}]*2)
inv = env.state[0]['observation']['market']['inventory']
print({k: 10000 - v for k, v in inv.items()})"
```

Three consequences worth keeping in mind:

* **The whole game is ~226k a season, split between two players.** v48's 168k is
  most of it. Any strategy has to be read against that ceiling, not against its
  own previous score.
* **Animal products are 130,164 of it — 57%.** Crops alone cannot get past ~96k
  gross even before the opponent takes a share.
* **Melon is the second-worst good on the board.** No shop demands it; its whole
  drain is the town centre's 1/day. Its 250 base and 6-unit tiles are a trap that
  `melon_farm` walked into, and the ladder score of 439 is what that costs.

Per-tile-day value, at base, from `yield_profile`:

```
MELON  129.09   CARROT 21.25   WHEAT 18.00   STRAWBERRY 22.35   TOMATO 15.83
```

Animals for comparison (1 unit per `interval` days, doubled by CARE, one tile):

```
COW    80-160 coins/tile/day     SHEEP 67-133     GOOSE 50-100
```

**Animals are 4-8× a crop tile.** That is the single biggest unexploited fact in
this file.

## 3. What was built

* `agentlib/game/market.py` — `drain_per_day`, `projected_drain_per_day`,
  `depth_to_price`, `base_price`.
* `agentlib/strategies/market_farm.py` — the strategy. Its docstring is the
  specification; read that, not this section.
* `agentlib/strategies/wheat_farm.py` — two behaviour-preserving seams
  (`_crops`, `_plant_action`) and `MAX_PLANTS_PER_UNIT` promoted to a class
  attribute, which `_classify` had been ignoring. Verified byte-identical:
  `melon_farm` vs v48 scored **31,935 before and after**.
* `agentlib/strategies/__init__.py` — `apply_params`, a `params:` block that
  overrides a strategy's UPPER_CASE constants per instance. **This is the new
  framework capability**: controllers were configurable, strategies were not, so
  no search could reach inside one.
* `tools/optimize.py` — `--space market_farm`.
* `tools/evaluate.py` — validates `params` strictly in the parent process.
* `tests/test_market_farm.py` — 32 tests. 170 pass overall, ruff clean.

## 4. Results so far

```
                          ours      opponent    record        margin
vs opponents/v48        26,743      138,935     0 W / 24 L   -112,192
  (melon_farm)          31,935      156,747     0 W / 24 L   -124,812

vs melon_farm           35,161       35,357     9 W / 11 L        -196
                                                              sd 7,085

sales/episode: WHEAT 354, CARROT 122, STRAWBERRY 83, MELON 24, TOMATO 13
```

Read honestly: the margin against v48 improves by 12,620 while our own score
*drops* 5,192, and against the monoculture it is a coin flip. The mechanism does
what it was built to do — five markets instead of two, melon capped at ~2 tiles
instead of 10 — and it has not yet bought anything.

**Why, and it is not a bug:** at ~36 plant slots the farm is labour-limited, not
demand-limited. Under a tile constraint every crop but melon is worth 16-22 coins
per tile-day, so spreading across five of them cannot beat concentrating in one.
Drain only starts binding once the tile budget is large. Two bugs were found and
fixed on the way there, both recorded in the code:

1. `_seed_targets` returned the tile shortfall where `_market` expects a stock
   level, so the farm re-bought the same shortfall every turn — 11 melon and 55
   wheat seeds by the end of day 0, 306 coins left of 3,000, **score 300**.
2. `_deficits` re-sorted by size of gap, discarding the value ranking `_plan` had
   just computed. Strawberry is short by 55 tiles and wheat by 3, so the largest
   gap is always the crop we wanted least.

And one design error: ranking crops purely by coins-per-tile-day is a
deterministic bankruptcy on day 0, because strawberry (22.35) outranks wheat
(18.00) while costing ten times as much seed and paying five days later. Fixed
with the `CAPITAL_WEIGHT` / `CAPITAL_EASE` blend — see `MarketFarm._score`.

## 5. The search to run

Nothing below has been run. Start here:

```bash
# 1. Baselines THROUGH A PROTOCOL, so they land in results/experiments.jsonl
#    and compare.py can pair them. Do this first; §4 above is not comparable.
make deps                                   # if this is a fresh clone
python tools/evaluate.py --strategy melon_farm  --protocol eval/protocols/v3.yaml
python tools/evaluate.py --strategy market_farm --protocol eval/protocols/v3.yaml
python tools/compare.py --vs melon_farm

# 2. The sweep. ~11 dimensions, so budget 300+ trials; at 60 episodes/trial and
#    ~2,600 episodes/hour that is roughly 7 hours per 300 trials at --jobs 3.
#    Scale --jobs to cores-1, not cores: 8 on a 4-core box caused the timeouts
#    that wasted an afternoon earlier in this project.
python tools/optimize.py --space market_farm --trials 300 \
    --protocol eval/protocols/v3.yaml --split train \
    --objective mean_margin --jobs 7 --study market-v3

# 3. Accept on the noise-aware objective, and only on holdout, ONCE.
#    Search with mean_margin, accept with score_lo — v3's header says why.
python tools/optimize.py --space market_farm --trials 0 --study market-v3
#    then re-run the winning params with --split holdout
```

The study is SQLite-backed with `load_if_exists`, so the command resumes if the
machine sleeps.

**What the result means.** If the sweep lands `MAX_QUADRANTS` at 3-4 and
`MAX_PLANTS_PER_UNIT` well above 4.0 and the score climbs, the labour-limit
reading is right and the third quadrant is only a trap for a wheat rotation. If
it lands `CAPITAL_WEIGHT` near 0, something is wrong elsewhere — that setting is
a measured bankruptcy. If the whole surface is flat, check the params actually
applied: `evaluate.py` validates them strictly in the parent, but confirm
`applied_params` is non-empty on a single run before trusting a flat result.

## 6. Next, in order of expected value

1. **A drain-sized `ranch_farm`.** 130,164 of the addressable market, and animal
   tiles are 4-8× crop tiles. The current `ranch_farm` holds `{"SHEEP": 11}` by
   hand; the herd should be sized the way crops now are — `drain_per_day(product)
   / units_per_animal_per_day` — which for wool at 8 shop instances is roughly 18
   sheep, and for milk roughly 9 cows. It should also sell FERTILIZER, which is
   free from `COLLECT_FERTILIZER` and which v48 turns into ~400 units a season.
   Note fertilizer's drain is exactly **zero**, so it needs metering more than
   anything else on the board.
2. **Pair the two under `AllocateController`.** The architecture already
   supports it and the controller docstring records that the split lost to
   melon_farm alone — but that was two strategies with overlapping *crop*
   demands. A crop specialist and an animal specialist compete for labour and
   land, not for the same market curve, which is the case allocation was built
   for and has not yet been tested on.
3. **Fertilizer as a yield doubler.** `WATER` on a fertilized tile adds +2. Only
   worth doing once animals exist to produce it.

## 7. On the RL plan

The GRPO design discussed before this work: the sample budget is fine (~2,600
episodes/hour, 25 days to the 30 Sep deadline), and the strategy-selection action
space is the problem — its ceiling is the best fixed strategy, and `allocate.py`
already measured that combining these specialists is monotonically worse than the
best one alone. `apply_params` changes the picture slightly: a policy over
`market_farm`'s *continuous knobs*, conditioned on state, is a far richer action
space than a policy over which strategy to run, and it is now expressible. The
sweep in §5 is also the honest baseline any learned policy has to beat — if
CMA-ES/TPE over these eleven numbers matches a network, the network is not
earning its keep.

Two corrections to the reward design, repeated here so they are not lost:
portfolio value must be **liquidation** value (integrated down the curve, clipped
at the 100-unit shed cap) or the shaping trains hoarding; and an exactly
telescoping shaping term is a **no-op** under outcome-level GRPO, because the
group-normalised advantage is unchanged — it only bites with per-step discounted
returns.
