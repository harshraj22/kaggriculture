# Kaggriculture — Game Spec (condensed reference)

Condensed from the competition "How to Play" page, for reading. **Not a source of truth** —
that is `kaggle_environments/envs/kaggriculture/kaggriculture.py`, which ships `README.md`
and `AGENTS.md` alongside it. Code must import from the env, never from this page.

Places this page is misleading, verified against the source:

- **Quadrant purchase order is fixed**: `LAND_ORDER = ["NE", "SW", "SE"]` at $1k/$2k/$4k.
  "Buy neighbouring quadrants" reads like a choice. It isn't.
- **Ongoing crops do have a `max_yield_day`** (tomato 8, strawberry 10); the table below
  prints "—" where the docs print "NA".
- **Animals use `max_held`** — a cap on currently-held `yield_units`, not lifetime output.

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
| Melon | one-time | 80 | 250 | 10 d | 12 d | — | 6 | 1 | 0.5 |
| Goose → Egg | ongoing | 300 | 50 | 4 d | — | daily | 4 | 1 + coop | 2 |
| Cow → Milk | ongoing | 400 | 160 | 8 d | — | every 2 d | 6 | 1 + pasture | 1 |
| Sheep → Wool | ongoing | 500 | 200 | 6 d | — | every 3 d | 6 | 1 + pasture | 0.67 |
| Fertilizer | — | 100 | — | — | — | — | — | 1 | — |

**Upkeep:** plants need WATER every day; 2 consecutive unwatered days → WEED.
Animals need FEED (wheat) every day; 2 consecutive unfed days → escape (gone).

## Actions

**Move:** `NORTH` `SOUTH` `EAST` `WEST`

**Shed** (must be orthogonally adjacent):
- `PICKUP <item> [n]` — shed → inventory. Seeds live in a separate slot, never picked up.
- `DROP` — dump entire inventory into shed. Overflow past `shedCapacity` discarded.

**Plants:**
- `PLANT <crop>` — seeds are shared/auto-available; if multiple units plant more than you hold, **none** are planted.
- `WATER` — once/day, repeats are no-ops.
- `HARVEST` — ≥1 unit; one-time crops are removed from the map after harvest.
- `FERTILIZE` — doubles the per-day yield bonus for 3 days, but only on days the plant is also watered.

**Animals:**
- `PLACE <item> [n]` — on a matching empty structure places 1 animal; adjacent to shed drops items into shed.
- `FEED` (once/day) · `HARVEST` · `COLLECT_FERTILIZER` (1/animal/day after CARE) · `CARE` (once/day)

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
- **Animal CARE:** at end of day, if fed AND cared → `pending_care_bonus += 2`. On a scheduled
  production day, if fed, the whole bank is added on top of the base 1 and resets. Unfed on a
  production day → no yield and the bank resets.

## Farm hands

- `HIRE` is a market order, per-day. Cost = `farmHandCostMult * fib(n)` where `n` = hires already
  made today → **1, 1, 2, 3, 5, 8, 13, 21, …** (resets each day).
- Hands vanish at end of day and drop inventory in the shed.
- Spawn orthogonally adjacent to the shed, NWSE preference.

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
- Town center consumes 1 of every product (not fertilizer) every `townCenterSellInterval = 12` turns;
  2× after day 10, 4× after day 20.

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

Town consumption **drains market inventory**, which **raises** prices. Demand grows monotonically.

## Market price model

```
price(inv) = base + sign · amp · f(|inv − I0|)
  sign = +1 if inv < I0 (scarcity → up),  −1 if inv > I0 (glut → down)
  amp  = target · base / f(T)
  f ∈ {linear, sq, sqrt, log, log10}     # log uses ln(1+x)
```

Floored at $1, rounded to nearest dollar. `I0 = 10,000` for everything.

| Resource | Base | T | Below f | Below tgt | Above f | Above tgt | P(I0−T) | P(I0+T) | P(I0+2T) |
|---|---|---|---|---|---|---|---|---|---|
| Wheat | 25 | 400 | sqrt | 0.80 | log | 0.20 | 45 | 20 | 19 |
| Carrot | 35 | 450 | log | 0.20 | sqrt | 0.70 | 42 | 10 | 1 |
| Tomato | 60 | 200 | linear | 0.40 | sqrt | 0.60 | 84 | 24 | 9 |
| Strawberry | 120 | 100 | sqrt | 0.70 | linear | 1.60 | 204 | 1 | 1 |
| Melon | 250 | 300 | log | 0.20 | sq | 3.60 | 300 | 1 | 1 |
| Egg | 50 | 332 | linear | 0.40 | log | 0.20 | 70 | 40 | 39 |
| Milk | 160 | 122 | sqrt | 0.60 | linear | 1.60 | 256 | 1 | 1 |
| Wool | 200 | 105 | log | 0.20 | sq | 3.20 | 240 | 1 | 1 |
| Fertilizer | 100 | 200 | linear | 0.40 | linear | 0.40 | 140 | 60 | 20 |

Key asymmetries:
- **Wheat** spikes on scarcity, barely moves on glut → safe to dump, expensive to buy for feed.
- **Carrot** barely moves on scarcity, **crashes** on glut.
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
