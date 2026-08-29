# Kaggriculture — Game Spec (condensed reference)

Condensed from the competition "How to Play" page, for reading. **Not a source of truth** —
that is `kaggle_environments/envs/kaggriculture/kaggriculture.py`, which ships `README.md`
and `AGENTS.md` alongside it. Code must import from the env, never from this page.

> **⚠️ The env changes under you. Pin your version and re-baseline after upgrades.**
> `tests/test_smoke.py::test_docs_price_table_matches_env` catches drift between
> this page and the installed env; `evaluate.py` records `env_version`/`env_hash`
> with every result and `compare.py` refuses to compare across them.

## Balance changelog

Staff have said 1.32.7 "should be the last change, excepting game breaking bugs."

**1.32.6 — town demand cut, shops drawn with replacement**
([discussion](https://www.kaggle.com/competitions/kaggriculture/discussion/733431),
[PR 1394](https://github.com/Kaggle/kaggle-environments/pull/1394))

- Town Center bought 2×/day with 2×/4× multipliers on days 10/20. Now **1×/day,
  flat, forever** — `TOWN_CENTER_DEMAND_SCHEDULE` is gone. Stated reason: heavy TC
  demand made markets "too resistant to sell pressure later in the game."
- Shops are now sampled **with replacement** (`MAX_SHOP_INSTANCES = 8`), so a game
  can roll 4× Yarn Store and zero Bakery. Unlock schedule and per-shop demand
  unchanged. **Games now differ from each other far more than before.**

**1.32.7 — `hinge` scarcity curve for carrot, tomato, egg**
([discussion](https://www.kaggle.com/competitions/kaggriculture/discussion/735311),
[PR 1399](https://github.com/Kaggle/kaggle-environments/pull/1399))

Intent: make these three "viable in some situations, not universally" — prices
spike when shop demand is high *and* nobody is producing. Staff's stated firing
rates assuming no production, and a competitor's independent replay measurement
over 120 episodes:

| | staff | measured |
|---|---|---|
| tomato | 50% of games | 55.0% |
| carrot | 26% | 28.3% |
| egg | 22% | 25.8% |

**The subtlety that matters most:** median scarcity sits just *below* each new
knee (219 vs 200 tomato, 316 vs 450 carrot, 228 vs 332 egg). So the **median game
is the old game** — dumping 100 units at median scarcity pays 1.00× the old
revenue. Only the tail moved: ~2× at p90, large multiples only in the deepest
games. Melon is untouched and serves as a clean control. Carrot's `below_target`
also moved 0.20 → 1.00, which the announcement didn't mention.

This is a **dispersion change, not an expectation change**, and that has a direct
consequence for how we optimise — see `notes/brainstorm.md`.

Places this page is misleading, verified against the source:

- **Quadrant purchase order is fixed**: `LAND_ORDER = ["NE", "SW", "SE"]` at $1k/$2k/$4k.
  "Buy neighbouring quadrants" reads like a choice. It isn't.
- **Ongoing crops do have a `max_yield_day`** (tomato 8, strawberry 10); the table below
  prints "—" where the docs print "NA".
- **Animals use `max_held`** — a cap on currently-held `yield_units`, not lifetime output.

## Turn budget

`actTimeout = 1` second per turn, plus a **60-second bank for the whole episode**
(`remainingOverageTime`); the framework deducts only the *excess* over 1s. So an
occasional slow turn is affordable — there is materially more room for search than
"1 second per turn" suggests, as long as the average stays under.

## Core loop

Two players, separate farms, **30 days × 24 turns = 720 turns**. Start with
**3000 coins**. Winner = most **coins in bank** at the end. Unsold inventory is worth nothing.

Each turn you submit:

```py
{"farmer": [ACTION, *args], "hands": [[ACTION, *args], ...], "market": [[ORDER, ITEM, N], ...]}
```

- One action per farmer/hand per turn.
- Up to `maxMarketOrdersPerTurn = 10` market orders per turn; extras silently dropped.

## Object table

| Type | Yield | Seed cost | Base price | 1st yield | Max yield day | Cadence | Max yield | Action cost | Max yield/tile/day |
|---|---|---|---|---|---|---|---|---|---|
| Wheat | one-time | 10 | 25 | 2 d | 4 d | — | 6 | 1 | 1.5 |
| Carrot | one-time | 20 | 35 | 2 d | 3 d | — | 4 | 1 | 1.333 |
| Tomato | ongoing | 50 | 60 | 8 d | — | daily | 4 | 1 | 4 |
| Strawberry | ongoing | 100 | 120 | 10 d | — | every 2 d | 4 | 1 | 2 |
| Melon | one-time | 80 | 250 | 10 d | 12 d* | — | 6 | 1 | 0.5 |
| Goose → Egg | ongoing | 300 | 50 | 4 d | — | daily | 4 | 1 + coop | 2 |
| Cow → Milk | ongoing | 400 | 160 | 8 d | — | every 2 d | 6 | 1 + pasture | 1 |
| Sheep → Wool | ongoing | 500 | 200 | 6 d | — | every 3 d | 6 | 1 + pasture | 0.67 |
| Fertilizer | — | 100 | — | — | — | — | — | 1 | — |

\* Melon reaches `max_yield` 6 at **age 10**, not 12: the last two days of the
documented window add nothing and are dead tile-days. Harvest on saturation, not
on the calendar — worth +1,676 to `melon_farm`.

**Upkeep:** plants need WATER every day; 2 consecutive unwatered days → WEED.
Animals need FEED (wheat) every day; 2 consecutive unfed days → escape (gone).

## Actions

**Move:** `NORTH` `SOUTH` `EAST` `WEST`

**Shed** — ⚠️ **the shed is not a tile you walk to.** It is the four centre squares,
`(4,4) (5,4) (4,5) (5,5)` on a 10×10 board (`_shed_access_tiles`). The env's helper
is *named* `_is_shed_adjacent` but tests membership in exactly those four, not
orthogonal adjacency to anything. Searching `tiles` for a `SHED` kind finds nothing.
- `PICKUP <item> [n]` — shed → inventory. Seeds live in a separate slot, never picked up.
- `DROP` — dump entire inventory into shed. Overflow past `shedCapacity` discarded.

**Plants:**
- `PLANT <crop>` — seeds are shared/auto-available; if multiple units plant more than you hold, **none** are planted.
- `WATER` — once/day, repeats are no-ops.
- `HARVEST` — ≥1 unit; one-time crops are removed from the map after harvest.
- `FERTILIZE` — doubles the per-day yield bonus for 3 days, but only on days the plant is also watered.

**Animals:**
- `PLACE <item> [n]` — on a matching empty structure places 1 animal; adjacent to shed drops items into shed.
- `FEED` (once/day) — ⚠️ consumes 1 WHEAT from the **acting unit's own inventory**,
  not from the shed. Somebody has to be *carrying* wheat when they reach a hungry animal.
- `HARVEST` · `COLLECT_FERTILIZER` (1/animal/day after CARE) · `CARE` (once/day)

⚠️ **`HARVEST` puts produce in the unit's inventory, and `SELL` only spends from the
shed.** Anything never carried back and `DROP`ped is worth zero. End-of-day auto-drop
covers this, but it delays every sale by up to a day and discards overflow past 100.

**Terrain:** `BUILD_COOP` · `BUILD_PASTURE` · `DIG` (remove plant/weed/structure)

**Other:** `PASS`

**Market orders:** `BUY_SEED` · `BUY_ANIMAL` · `BUY_PRODUCT` (WHEAT and FERTILIZER only) ·
`SELL` · `HIRE` · `BUY_LAND` ($1k, $2k, $4k)

## Yield rules

- **One-time crops** (wheat, carrot, melon): from `ceil(max_yield_day / 2)` onward, each
  watered day adds **+1** to total harvestable yield; fertilized days add **+2**.
  Max lifespan = `max_yield_day + 1` day; after that yield decays 1 every other turn to 0 → weed.
- **Ongoing crops** (tomato, strawberry): base **1** per scheduled production; **2** if
  fertilized AND watered that day. Decay starts one day after cumulative productions hit `max_yield`.
- **Animal CARE:** at end of day, if fed AND cared → `pending_care_bonus += 1`.
  (The rulebook said +2; staff confirmed +1 is correct and fixed the docs — discussion 732450.) On a scheduled
  production day, if fed, the whole bank is added on top of the base 1 and resets. Unfed on a
  production day → no yield and the bank resets.

## Farm hands

- `HIRE` is a market order, per-day. Cost = `farmHandCostMult * fib(n)` where `n` = hires already
  made today → **1, 1, 2, 3, 5, 8, 13, 21, …** (resets each day).
- Hands vanish at end of day and drop inventory in the shed.
- Spawn on the shed access tiles, NWSE preference.
- ~8 hands ≈ $54/day. Gating hiring behind a cash threshold is a **false economy**: an
  unworked farm loses plants outright, since two unwatered days kills them.

Hands are extraordinarily cheap relative to crop value — the first ~6 hands of a day cost 20 coins
total and buy you 6 × 24 = 144 extra actions. **Labor is the cheapest resource in the game.**

## Map

- `boardSize = 10` → 10×10 grid, four 5×5 quadrants. You start owning one; buy the rest for $1k/$2k/$4k.
- Weeds spawn on empty unlocked tiles with `weedSpawnChance = 0.005` per tile per day.
- Shed holds **100** non-seed items; overflow at end-of-day drop is **discarded**.
- You can see the opponent's farm tiles and money, but **not** their shed/seeds/inventories.

## Town demand

- New shop unlocks every `townShopUnlockInterval = 3` days, randomly chosen, permanent.
- Each unlocked shop consumes 1 of each product it demands every `townShopSellInterval = 4` turns
  (= 6/day). Single-product shops consume 2×.
- Town center consumes 1 of every product (not fertilizer) every `townCenterSellInterval = 12`
  turns — **flat, for the whole game**. The old 2×-after-day-10 / 4×-after-day-20 ramp was
  removed in 1.32.6.
- Shops are drawn **with replacement**, so shop composition varies wildly between games.
  Plan for "this game happens to have 3× Yarn Store", not for the average game.

| Shop | Demands |
|---|---|
| Bakery | eggs, wheat |
| Pizza Shop | milk, tomatoes, wheat |
| Brunch Spot | eggs, wheat, strawberries |
| Yarn Store | wool (2×) |
| Ice Cream Shop | strawberries, milk, wheat |
| Pet Cafe | carrots (2×) |
| Smoothie Shop | strawberries, milk |
| Farmers Market | wheat, carrots, tomatoes, strawberries |

Town consumption **drains market inventory**, which **raises** prices. Total demand still
grows as shops unlock, but **it no longer accelerates late** — the town-center ramp is gone,
so "hold produce for a hungrier late market" is a much weaker argument than it was.

## Market price model

```
price(inv) = base + sign · amp · f(|inv − I0|)
  sign = +1 if inv < I0 (scarcity → up),  −1 if inv > I0 (glut → down)
  amp  = target · base / f(T)
  f ∈ {linear, sq, sqrt, log, log10, hinge}     # log uses ln(1+x)
```

`hinge` (added in 1.32.7) is linear in x/T below the knee and quadratic above it,
with `HINGE_GAIN = 8.0` — calm right up until the resource is genuinely scarce,
then it runs away. `f(T) == 1` by construction, so `target` means the same thing
as for every other shape, which is why the P(I0−T) column only moves where
`below_target` also moved.

Floored at $1, rounded to nearest dollar. `I0 = 10,000` for everything.

| Resource | Base | T | Below f | Below tgt | Above f | Above tgt | P(I0−T) | P(I0+T) | P(I0+2T) |
|---|---|---|---|---|---|---|---|---|---|
| Wheat | 25 | 400 | sqrt | 0.80 | log | 0.20 | 45 | 20 | 19 |
| Carrot | 35 | 450 | **hinge** | **1.00** | sqrt | 0.70 | **70** | 10 | 1 |
| Tomato | 60 | 200 | **hinge** | 0.40 | sqrt | 0.60 | 84 | 24 | 9 |
| Strawberry | 120 | 100 | sqrt | 0.70 | linear | 1.60 | 204 | 1 | 1 |
| Melon | 250 | 300 | log | 0.20 | sq | 3.60 | 300 | 1 | 1 |
| Egg | 50 | 332 | **hinge** | 0.40 | log | 0.20 | 70 | 40 | 39 |
| Milk | 160 | 122 | sqrt | 0.60 | linear | 1.60 | 256 | 1 | 1 |
| Wool | 200 | 105 | log | 0.20 | sq | 3.20 | 240 | 1 | 1 |
| Fertilizer | 100 | 200 | linear | 0.40 | linear | 0.40 | 140 | 60 | 20 |

Key asymmetries:
- **Wheat** spikes on scarcity, barely moves on glut → safe to dump, expensive to buy for feed.
- **Carrot** — as of 1.32.7 it now *spikes hard* on scarcity (`hinge`, target 1.00,
  up from `log`/0.20) while still crashing on glut. That inverts the old read of
  carrot as a pure dump-and-crash crop: draining carrot supply is now valuable.
- **Premium goods** (strawberry, melon, milk, wool) have `above_target > 1` → even modest gluts
  hit the $1 floor. Sale timing and drip-feeding matter enormously for these.
- Sells are quoted **pre-sell**, buys **post-buy** → buy-then-sell round trip nets exactly 0.
- At the $1 floor, sold units are **not** added to market inventory.
- Orders process one unit at a time, interleaved between the two players.

## Turn processing order

1. Validate actions → 2. Player actions (simultaneous) → 3. Market queue (in order, per player,
interleaved) → 4. Town consumption → 5. Observation update (day refresh → market refresh →
income → farm update)

## Observation

```py
{
  "player": int, "day": int, "hour": int,
  "farms": [farm, farm],
  "market": {"inventory": {...}, "prices": {...}},
  "town":   {"unlocked_shops": [...]},
  "private": {"shed": {...}, "seeds": {...}, "inventories": [farmer_inv, hand_inv, ...]},
}
```

`farm`: `{money, tiles[y][x], farmer:[x,y], hands:[[x,y],...], unlocked_quadrants, hires_today}`

`tile` ∈ `None` | `"LOCKED"` | plant dict | `{"kind":"WEED"}` | structure dict

```py
# plant
{"kind":"PLANT","crop":...,"planted_day":int,"watered_today":bool,
 "consecutive_unwatered":int,"yield_units":int,"max_lifespan_step":int,
 "fertilized_until_day":int}
# structure
{"kind":"COOP"|"PASTURE","animal":"GOOSE"|"COW"|"SHEEP"|None,"placed_day":int,
 "yield_units":int,"fed_today":bool,"consecutive_unfed":int,"cared_today":bool,
 "fertilizer_available":bool,"pending_care_bonus":int}
```

## Configuration defaults

| Param | Default |
|---|---|
| episodeSteps | 720 |
| boardSize | 10 |
| startingMoney | 3000 |
| maxMarketOrdersPerTurn | 10 |
| turnsPerDay | 24 |
| shedCapacity | 100 |
| weedSpawnChance | 0.005 |
| townShopUnlockInterval | 3 |
| townShopSellInterval | 4 |
| townCenterSellInterval | 12 |
| seed | null |

Per-resource market overrides can be passed via `env.configuration["marketParams"]`.
