# ranch_farm — handoff

Companion to `notes/market_farm.md`. Same environment and caveats: kaggle-
environments 1.32.7, direct `env.run` over seeds 1..N with both seats (so 2N
paired episodes), opponent `opponents/v48/main.py`. **None of this went through
`tools/evaluate.py`, so none of it is in `results/experiments.jsonl`** — re-run
the baselines through a protocol before comparing them to anything that is.

## 1. Animal economics, measured

Driving `_daily_refresh_animals` directly for 24 days, fed every day, harvested
on sight — and separately with `CARE` withheld:

| animal | product | u/day **cared** | u/day **uncared** | coins/day/tile | unit-turns/day | **coins/unit-turn** | cost |
|---|---|---:|---:|---:|---:|---:|---:|
| SHEEP | WOOL | 1.25 | 0.29 | 250 | 2.33 | **114** | 500 |
| COW | MILK | 1.25 | 0.38 | 200 | 2.50 | 96 | 400 |
| GOOSE | EGG | 1.83 | 0.88 | 92 | 3.00 | 33 | 300 |

A crop tile is worth 16-22 coins/day. **A sheep tile is worth 250.**

Three findings, all of which changed the code:

* **CARE is worth 4.3x on sheep, 3.3x on cows.** The engine accrues
  `pending_care_bonus` +1 per fed-and-cared day and spends the whole accrual on
  the next production. Skipping care costs most of the yield, not a little.
* **Harvest on sight.** Steady production is `min(max_held, 1 + interval)` every
  `interval` days — 4 per 3 days for a sheep into a cap of 6. Over 24 days:
  on sight **30 units**, every 4 days 20, every 6 days 18. The old trigger
  (`yield_units >= max_held - 1`) was throwing away a third of the output.
* **Rank by unit-turn, not by tile.** A ranch has tiles spare and no labour
  spare. Per tile the three animals look close; per unit-turn a sheep is 3.5x a
  goose, because a goose must be picked up daily.

Reproduce the table:

```bash
.venv/bin/python -c "
from kaggle_environments.envs.kaggriculture import kaggriculture as e
def sim(a, days=24, care=True):
    A=e.ANIMALS[a]; t={'kind':A['structure'],'animal':a,'placed_day':0,'yield_units':0,
      'fed_today':False,'cared_today':False,'consecutive_unfed':0,'fertilizer_available':False}
    f={'tiles':[[t]]}; got=0
    for d in range(days):
        t['fed_today']=True; t['cared_today']=care; e._daily_refresh_animals(f,d)
        got+=t['yield_units']; t['yield_units']=0
    return got/days
for a in e.ANIMALS: print(a, round(sim(a),2), round(sim(a,care=False),2))"
```

## 2. Results

12 seeds x both seats unless noted.

```
                             mean       sd
ranch_farm  (this)         16,983   10,960
ranch_farm  (previous)      2,604    2,392   <- 6 seeds
market_farm                26,743    7,852
melon_farm                 31,935            <- current submission
```

The previous ranch_farm's documented **32,804** was measured on protocol v1
against a weak opponent. Against v48 it is 2,604. That gap is worth remembering
whenever an old number is quoted: **animal strategies collapse against a
competitor who also sells wool and milk**, and nothing in the v1 protocol
exercised that.

### Manual probes (6 seeds x both seats)

```
mix / hands / quadrants                      mean       sd
SHEEP+COW, 14 hands, 0.8/animal, 1 quad    23,330   10,234   <- default
SHEEP only, 14 hands, 0.8/animal           24,511   14,407
SHEEP only, 10 hands, 0.6/animal           16,066   13,861
SHEEP+COW+GOOSE, 14 hands, 0.8/animal       5,679    7,218
SHEEP+COW+GOOSE,  8 hands, 0.45/animal      4,662    1,755
SHEEP only, 14/0.8, 2 quadrants             6,024    5,667
SHEEP only, 14/0.8, HERD_SATURATION 1.6    11,309   15,005
```

Reading:

* **Hands are the dominant lever.** 0.45/8 -> 4,662 and 0.80/14 -> 23,330 on the
  same herd. What they buy is not scale but *reliability*: two missed meals and
  the animal escapes with its capital, so a crew sized to the average day loses
  the herd on the bad ones. This is the exact opposite of `wheat_farm`, where
  more hands measured strictly worse — its failure mode is travel, a ranch's is
  starvation.
* **Sheep-only and sheep+cow tie on the mean; sheep+cow has 30% less spread.**
  The default takes the lower variance, because the ladder is Bradley-Terry over
  win/loss and a wider spread at the same mean is rating-negative.
* **Geese poison any mix they are in**, exactly as coins-per-unit-turn predicts.
* **The second quadrant loses** (24,511 -> 6,024): 1,000 coins of land the herd
  is too labour-limited to fill, from the same purse the animals come from.
* **Overshooting the drain loses** (1.0 -> 23,330, 1.6 -> 11,309). Wool's curve
  is `sq/3.2`; the surplus does not fetch a lower price, it fetches nothing.

## 3. Bugs found, all measured

1. **CARE promoted above PICKUP** — 13 animals on day 22, **1 on day 23**, 4,627
   final. `FEED` spends from the acting unit's inventory, so a unit holding no
   wheat cannot feed; it falls through to CARE, which it can do, and is consumed
   there. Nobody hauls, everything starves. *A job that enables the survival job
   outranks any job that merely improves yield, however large the yield.*
2. **Structures treated as interchangeable** — a goose stranded in the shed for
   eight days beside sixteen empty pastures, each emitted as a `PLACE` target
   where the unit arrived and returned PASS. Those wasted assignments outranked
   CARE and HARVEST.
3. **Last-day feed churn** — `_sell_quantity` dumps the shed on the last day,
   `_feed_wanted` sees an empty barn and rebuys, 24 times: 695 wheat bought and
   548 sold on a herd of four.
4. **Shed-pressure valve overrode the sell throttle** — returning the whole shelf
   above 75% capacity took wool from 200 to the 1-coin floor to free five slots.
   Now releases only the excess. (Same fix applied to `market_farm`.)
5. **`_sell` priced non-tradeable shed items** — under `AllocateController` a
   rancher's `BUY_ANIMAL` leaves a live SHEEP in the shared shed, `market_farm`
   asked what a sheep is worth, and `KeyError: 'SHEEP'` struck it out of the
   episode. Fixed in `WheatFarm._sell`, which now skips anything not in
   `MARKET_PARAMS`.
6. **`AllocateController.granted` credited slot 0 unconditionally** — a run where
   `market_farm` was disabled on day 6 and `ranch_farm` played the other 23 days
   reported `granted: [4781, 4]`, the exact opposite of the truth. This is what
   sent me looking in the wrong place for an hour; fixed.

## 4. The combination does not work yet, and now we know why

```
market_farm alone          27,425
ranch_farm alone           22,686
allocate 0.5 / 0.5         14,183
allocate 0.6 / 0.4         14,093
```

Not the labour split — the **shared shed**. Both strategies act on one farm and
hold opposite, individually correct intentions about wheat: the ranch buys it to
feed the herd and reserves it from sale; the crop farm sees wheat in the shed and
sells it, because that is what a crop farm does and it does not know a herd
exists. Result: **6,045 wheat bought and sold per episode**, paying the spread on
every lap, and the herd starves anyway.

Netting opposing orders inside `_dedupe_orders` was tried and removed: worth ~3
coins in 14,000, because the buy and the sell land on different turns.

**The fix worth designing is a shed reservation** — a strategy declaring "this
stock is spoken for" that the arbiter honours before any other strategy's `_sell`
sees it. It is the smallest version of the "strategies must negotiate" change
`allocate.py` has always said it could not make, and there is now a concrete case
to design against. Until then, the two specialists are worth more apart.

## 5. The search to run

```bash
# Baselines through a protocol first — section 2 above is not comparable to
# anything in results/experiments.jsonl.
python tools/evaluate.py --strategy ranch_farm  --protocol eval/protocols/v3.yaml
python tools/evaluate.py --strategy market_farm --protocol eval/protocols/v3.yaml
python tools/compare.py --vs melon_farm

# 13 dimensions, so budget 400+ trials. --jobs now defaults to cores-1; set
# $KAGGRICULTURE_JOBS once if the box wants something else.
python tools/optimize.py --space ranch_farm --trials 400 \
    --protocol eval/protocols/v3.yaml --split train \
    --objective mean_margin --study ranch-v3

# Search with mean_margin, accept with score_lo, and only on holdout, once.
python tools/optimize.py --space ranch_farm --trials 0 --study ranch-v3
```

**What to look for.** `MAX_HANDS` and `HANDS_PER_ANIMAL` should land high; if the
search pushes them to the top of the range (20 / 1.5) the ceiling has not been
found and the range should be widened rather than the result accepted. If it
picks a mix containing GOOSE, that contradicts every manual probe and is worth
investigating before trusting. Given the variance here — sd is two thirds of the
mean — **prefer `score_lo` over `mean_margin` for the final choice** more strongly
than for `market_farm`: a strategy that sometimes loses its herd is exactly the
"raises the mean and raises the variance" case the forum flagged as
rating-negative.

## 6. Next

1. **Shed reservations**, per section 4. It unlocks the crops+animals
   combination, which is the only route to v48's ~168k: crops alone cap near 96k
   gross and animals near 130k, and both are shared with the opponent.
2. **Fertilizer is under-exploited.** It sells 234/episode already as a free
   byproduct, its drain is exactly zero so the throttle is doing real work, and
   `WATER` on a fertilized tile adds +2 instead of +1 — a yield doubler for
   `market_farm` that only a ranch can supply. That is the second synergy the
   shared shed would unlock, and unlike wheat it is not contested.
3. **Variance reduction.** Find what distinguishes a 45,600 episode from a 6,297
   one. The hypothesis is a herd-loss event; `agentlib` already journals per-turn
   money, so a diff of the two trajectories should show it in one pass.
