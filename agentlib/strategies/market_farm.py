"""MarketFarm — size the crop to the market's thirst, not to the tile's yield.

This docstring is the specification. `MarketFarm` inherits the whole per-turn
engine of `WheatFarm` — tile classification, priority order, unit assignment,
hiring, land, execution — and replaces one thing: **what to grow, and how fast
to sell it**. Read `wheat_farm.py` for the base algorithm.

## The measurement that produced it

`melon_farm` against the committed public agent (`opponents/v48`), 12 seeds ×
both seats, kaggle-environments 1.32.7:

    ours   31,935        v48   156,747        0 W / 0 T / 24 L
    margin -124,812   sd 29,410   best case -50,533

Their sell log against ours, one episode:

    ours    34 SELL actions:  WHEAT 668, MELON 120
    v48    572 SELL actions:  STRAWBERRY 430, FERTILIZER 397, MILK 335,
                              WHEAT 313, WOOL 179, MELON 72, CARROT 57

And the market at the final bell:

    MELON      inventory 10,156  ->  price   7   (base 250)
    MILK       inventory  9,874  ->  price 258   (base 160)
    STRAWBERRY inventory  9,859  ->  price 220   (base 120)
    TOMATO     inventory  9,754  ->  price 100   (base  60)

Every good with shop demand ended the season UNDER-supplied and above base. The
one good we concentrated on ended flooded at 3% of base. We were not out-farmed;
we were out-*sold*, into markets we never touched.

## The model: a good is worth its drain, not its base price

The town removes stock every `townShopSellInterval` steps (each unlocked shop
instance takes one of every product it demands, doubled for a shop that demands
only one) and every `townCenterSellInterval` steps (one of each town-centre
product). That is the only thing that pulls inventory back down. Sell at the
drain rate and the price never leaves `base`; sell faster and you are walking
your own price down for the rest of the episode, because nothing clears it.

Measured over 5 full episodes with both seats passing — so this is the town's
appetite alone, with no player supply at all:

    good         base   drain/season   season value at base   above_func
    WOOL          200            368                 73,680   sq/3.2
    STRAWBERRY    120            448                 53,712   linear/1.6
    MILK          160            278                 44,544   linear/1.6
    WHEAT          25            556                 13,890   log/0.2
    TOMATO         60            217                 13,032   sqrt/0.6
    EGG            50            239                 11,940   log/0.2
    CARROT         35            235                  8,232   sqrt/0.7
    MELON         250             30                  7,500   sq/3.6
    FERTILIZER    100              0                      0   linear/0.4
    ------------------------------------------------------------------
    TOTAL                                            226,530

That table is the game. The whole addressable market is ~226k a season, shared
between two players, and v48's 168k is most of it. **Melon is the second-worst
good on the board** — no shop demands it, so its entire drain is the town
centre's 1 unit a day — and it carries the harshest glut curve in the game
(`sq`, `above_target 3.60`). `melon_farm` sized its plot from tile yield and
market *depth*, and both of those said "melon"; drain says the opposite, and
drain is what actually pays.

## The algorithm

Two numbers per crop, both derived from the engine rather than tuned:

**Yield rate** — units per tile-day over one plant's whole life, from `CROPS`:

    WHEAT       4 units /  5 tile-days = 0.800     (one-time)
    CARROT      3 units /  4 tile-days = 0.750     (one-time)
    MELON       6 units / 11 tile-days = 0.545     (one-time)
    TOMATO      4 units / 12 tile-days = 0.333     (ongoing)
    STRAWBERRY  4 units / 17 tile-days = 0.235     (ongoing)

**Saturating tile count** — `projected_drain_per_day / yield_rate`, the number of
tiles whose steady output exactly matches the town's steady thirst. At a typical
mid-season four shop instances that is ~20 tiles of wheat, ~13 of carrot, ~21 of
tomato, ~55 of strawberry — and **1.8 of melon**. `melon_farm` held ten.

Then, per turn:

1. Rank the eligible crops by value per tile-day, `yield_rate × CURRENT price −
   seed/cycle`. Current price, not base: if the opponent floods strawberry, our
   strawberry ranking falls by itself and tiles move to carrot. That is the only
   opponent-conditioning in here and it costs nothing.
2. Walk the ranking, giving each crop up to `SATURATION ×` its saturating count,
   until the plant budget (`MAX_PLANTS_PER_UNIT` per unit, as in the base) runs
   out. Crops that cannot mature in `days_left` are skipped.
3. Buy seed toward the plan in the same order, so the best crop wins the budget.
4. Sow, each turn, whichever crop is furthest below its planned share.
5. Sell each good down to `SELL_FLOOR_RATIO × base` and no further, using
   `depth_to_price` — an absolute floor, not a fraction of the current price, so
   a market that is already crashed stops receiving stock instead of receiving
   half as much.

Because tiles are re-ranked every turn against live prices, the mix drifts on its
own: no schedule, no phases, no switching.

## Two smaller mechanisms, both worth their complexity

**Harvest ongoing crops at saturation, not on sight.** An ongoing plant produces
`max_yield` units in total, `+1` per production event, and `HARVEST` only resets
the counter — waiting does not cost units, it costs nothing. The base `_ripe`
returns True as soon as `yield_units > 0`, which is four harvest trips per
strawberry where one would do. Since travel is ~60% of measured unit-turns and
labour is what caps the farm, `_ripe` here holds until the plant is full or about
to die (`max_lifespan_step` is set the moment the last production lands, and
`_decay_plants` then eats a unit every other step, so "about to die" is a real
deadline and not a stylistic choice).

**Shed pressure overrides the sell floor.** The shed caps at 100 and the
end-of-day drop DISCARDS the overflow. Stock held back for a better price that
never arrives is worth zero, which is strictly worse than the floor price, so
above `SHED_PRESSURE` of capacity the throttle is switched off.

## What this deliberately does not do

No animals. WOOL + MILK + EGG are 130,164 of the 226,530 addressable — the larger
half of the board — and they need pastures, coops, feed and hauling, which is
`ranch_farm`'s machinery, not this one's. A crops-only portfolio can address at
most ~96k before the opponent takes their share, so **this strategy's ceiling is
roughly half the game.** Pairing it with a drain-sized `ranch_farm` under
`AllocateController` is the obvious next step and is why the split lives in the
controller.

No fertilizer either. `WATER` on a fertilized tile adds +2 instead of +1, and
animals produce fertilizer for free via `COLLECT_FERTILIZER` — v48 sold 397 of it
on top of using it. That is a yield doubler we are not collecting.

## Status: the mechanism works, the numbers do not yet

Untuned, protocol-free smoke runs, 12 seeds × both seats:

    vs opponents/v48   ours 26,743   v48 138,935   0/24   margin -112,192
    (melon_farm        ours 31,935   v48 156,747   0/24   margin -124,812)

    vs melon_farm      ours 35,161   melon 35,357   9 W / 11 L   margin -196

So against the public agent the margin closes by 12,620 while our own score goes
*down* by 5,192 — we take less of a market v48 is no longer allowed to corner —
and head-to-head against the monoculture it is a coin flip. The diversification
is real (five markets against melon_farm's two) and it has not yet bought
anything.

That is the expected result and the docstring above says why: **at ~36 plant
slots the farm is labour-limited, not demand-limited**, and under a tile
constraint every crop except melon is worth 16-22 coins per tile-day. Spreading
across five of them cannot beat concentrating in one until the tile budget grows
past the point where drain starts binding. The three things that could move it,
in the order worth trying:

1. **More tiles and hands.** `MAX_QUADRANTS`, `MAX_HANDS` and
   `MAX_PLANTS_PER_UNIT` are inherited from `wheat_farm`, where 2 / 8 / 4.0 were
   measured against a wheat rotation that replants every five days. A portfolio
   holding ongoing crops replants far less. All three are in the search space.
2. **Animals.** An animal tile yields 67-160 coins/day against a crop tile's
   16-22, and WOOL + MILK + EGG are 130,164 of the 226,530 addressable. This is
   the big one and it is not a `market_farm` change.
3. **Fertilizer**, which doubles `WATER` yield and falls out of animals free.

The constants below are starting points read off the engine, **not measured
optima**. `tools/optimize.py --space market_farm` searches them. See
`notes/market_farm.md` for the full handoff.
"""

from ..game.config import CROPS, MARKET_I0, SHED_CAPACITY, market_price
from ..game.market import base_price, depth_to_price, projected_drain_per_day
from ..game.observation import Obs
from .wheat_farm import PLANT, WheatFarm

#: Crops the portfolio may hold, in no particular order — step 1 ranks them.
MIX = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON")

#: Multiplier on each crop's saturating tile count. 1.0 means "produce exactly
#: what the town drinks and keep the price at base". Above 1.0 deliberately
#: overshoots into the falling part of the curve, which is right when tiles are
#: plentiful and a lower price on more units still wins; below 1.0 leaves room
#: for the opponent's supply, since the drain is shared and their stock walks our
#: price down just as ours does.
SATURATION = 1.0

#: How hard to rank crops by return on CAPITAL rather than return on tile-days
#: while cash is scarce. 0 disables it. See `_score`; this is the constant that
#: turned a deterministic bankruptcy into a working farm.
CAPITAL_WEIGHT = 1.0

#: Cash at which capital stops being the binding constraint and the ranking
#: reverts to pure tile-days. Roughly "enough to seed the whole board twice over
#: and still make payroll".
CAPITAL_EASE = 6000.0

#: Floor for metered selling, as a fraction of the good's BASE price.
SELL_FLOOR_RATIO = 0.7

#: Fraction of shed capacity above which the floor is ignored. Overflow is
#: discarded at end of day, and zero is worse than any price.
SHED_PRESSURE = 0.75

#: Days of headroom a crop needs beyond `first_yield_day` to be worth sowing.
#: One is the minimum that makes sense (mature on the final day); more protects
#: against a plant that matures with no turn left to harvest and sell it.
LEAD_SLACK_DAYS = 2


def yield_profile(crop: str) -> tuple[float, int]:
    """`(units per tile-day, tile-days per plant)` over one plant's whole life.

    Derived from `CROPS`, never transcribed, because the two crop families count
    completely differently and hand-copied numbers were wrong twice already.

    *One-time* crops are created holding 1 unit and gain +1 per watered day
    inside `[(max_yield_day + 1) // 2, max_yield_day]`, capped at `max_yield`.
    They can therefore saturate BEFORE the calendar deadline — melon reaches its
    6 at age 10 against a `max_yield_day` of 12 — and holding past saturation
    just burns tile-days. The `+1` on the denominator is the sowing day, which
    yields nothing.

    *Ongoing* crops start at 0 and gain +1 every `interval` days from
    `first_yield_day`, stopping after `max_yield` production events, at which
    point the engine sets `max_lifespan_step` and the plant dies. Total lifetime
    yield is exactly `max_yield` however often it is harvested.
    """
    spec = CROPS[crop]
    if spec["ongoing"]:
        units = spec["max_yield"]
        days = spec["first_yield_day"] + (units - 1) * max(1, spec["interval"]) + 1
        return units / days, days

    window_start = (spec["max_yield_day"] + 1) // 2
    saturation_age = window_start + spec["max_yield"] - 2
    harvest_age = min(spec["max_yield_day"],
                      max(spec["first_yield_day"], saturation_age))
    units = min(spec["max_yield"], 1 + max(0, harvest_age - window_start + 1))
    days = harvest_age + 1
    return units / days, days


class MarketFarm(WheatFarm):
    name = "market_farm"

    MIX = MIX
    SATURATION = SATURATION
    SELL_FLOOR_RATIO = SELL_FLOOR_RATIO
    SHED_PRESSURE = SHED_PRESSURE
    LEAD_SLACK_DAYS = LEAD_SLACK_DAYS
    CAPITAL_WEIGHT = CAPITAL_WEIGHT
    CAPITAL_EASE = CAPITAL_EASE

    #: `PREMIUM` is the base class's two-crop seam and this strategy does not use
    #: it — the mix is a ranking, not a headline crop plus filler. Left None so
    #: `_premium_alive` and the inherited `_crop_for` stay inert.
    PREMIUM = None

    # --- what to grow ---------------------------------------------------------

    def _crops(self) -> list[str]:
        return [c for c in self.MIX if c in CROPS]

    def _price(self, obs: Obs, crop: str) -> float:
        """Live price, from the observation where the env offers it."""
        px = obs.prices.get(crop)
        if px:
            return float(px)
        return float(market_price(crop, obs.market_inventory.get(crop, MARKET_I0)))

    def _score(self, obs: Obs, crop: str) -> float:
        """Rank a crop by return on whichever resource is currently scarce.

        Ranking on coins per tile-day alone is what a farm with money should do,
        and it is a **deterministic bankruptcy** on day 0. Measured: the first
        version of this strategy scored exactly 300 with 40 tiles left empty. Per
        tile-day, strawberry (22.35) beats wheat (18.00), so the plan asked for 34
        strawberry tiles, `_seed_targets` spent 2,700 of a 3,000 purse on seed
        that returns nothing until day 10, and the farm never made payroll again.

        Per coin of seed per day the ordering inverts completely, because seed
        cost spans an order of magnitude and time-to-yield spans five days to ten:

            crop         coins/tile-day    coins per seed-coin per tile-day
            WHEAT                 18.00                              1.800
            CARROT                21.25                              1.063
            MELON                129.09                              1.614
            TOMATO                15.83                              0.317
            STRAWBERRY            22.35                              0.224

        Neither ranking is right on its own: capital binds in week one, tile-days
        and labour bind from week two, and the crossover is exactly what a search
        should find rather than what a schedule should assert. So the score is a
        geometric blend whose exponent fades out as the purse fills — at
        `CAPITAL_EASE` and above it is pure coins per tile-day, and the geometric
        (rather than arithmetic) form is what lets two quantities in different
        units be blended without inventing a conversion factor between them.
        """
        spec = CROPS[crop]
        rate, cycle = yield_profile(crop)
        tile_rate = rate * self._price(obs, crop) - spec["seed"] / cycle
        if tile_rate <= 0:
            return 0.0

        weight = self.CAPITAL_WEIGHT
        if self.CAPITAL_EASE > 0:
            scarcity = 1.0 - min(1.0, max(0.0, obs.money) / self.CAPITAL_EASE)
            weight *= scarcity
        weight = min(1.0, max(0.0, weight))
        if weight <= 0.0:
            return tile_rate

        capital_rate = tile_rate / max(1.0, float(spec["seed"]))
        return tile_rate ** (1.0 - weight) * capital_rate ** weight

    def _plan(self, obs: Obs) -> dict[str, int]:
        """Target live-tile count per crop. Recomputed every turn from prices.

        Deliberately not cached. The base engine is stateless by construction and
        the ranking is five crops wide, so re-deriving it costs less than the
        invalidation logic a cache would need — and a stale plan would keep
        sowing into a market the opponent flooded two days ago.
        """
        budget = int((1 + len(obs.hands)) * self.MAX_PLANTS_PER_UNIT)
        if budget <= 0:
            return {}

        ranked = []
        for crop in self._crops():
            spec = CROPS[crop]
            if obs.days_left < spec["first_yield_day"] + self.LEAD_SLACK_DAYS:
                continue  # cannot mature; the seed and the tile are both wasted
            rate, _cycle = yield_profile(crop)
            if rate <= 0:
                continue
            value = self._score(obs, crop)
            if value <= 0:
                continue  # the seed costs more than the crop will fetch
            drain = projected_drain_per_day(crop, obs.unlocked_shops, obs.days_left)
            cap = round(self.SATURATION * drain / rate)
            if cap > 0:
                ranked.append((value, crop, cap))

        # Highest score first, crop name as a deterministic tiebreak so two crops
        # at an identical price do not alternate between turns.
        ranked.sort(key=lambda item: (-item[0], item[1]))

        plan: dict[str, int] = {}
        for _value, crop, cap in ranked:
            if budget <= 0:
                break
            take = min(cap, budget)
            plan[crop] = take
            budget -= take
        return plan

    def _alive_by_crop(self, obs: Obs) -> dict[str, int]:
        counts: dict[str, int] = {}
        for tile in obs.owned_tiles():
            if tile.is_plant:
                crop = tile.get("crop")
                if crop:
                    counts[crop] = counts.get(crop, 0) + 1
        return counts

    def _deficits(self, obs: Obs, queued: dict[str, int]) -> list[tuple[int, str]]:
        """`(shortfall, crop)` for every crop under its planned share.

        Returned in PLAN order — which is value order — and deliberately not
        re-sorted by shortfall. An earlier version sorted by size of gap, which
        threw away the ranking `_plan` had just computed: strawberry is short by
        55 tiles and wheat by 3, so the biggest gap is always the crop we decided
        we wanted least, and it took the seed budget every turn.
        """
        alive = self._alive_by_crop(obs)
        out = []
        for crop, target in self._plan(obs).items():
            short = target - alive.get(crop, 0) - queued.get(crop, 0)
            if short > 0:
                out.append((short, crop))
        return out

    def _seed_targets(self, obs: Obs, jobs: dict) -> dict[str, int]:
        """Seed HOLDINGS to aim at, in plan order.

        The contract is what `_market` implements: it buys `target − held`, so
        these are stock levels, **not** amounts to buy. Returning "the shortfall
        in tiles" instead is a slow, total bankruptcy and it is not obvious from
        reading either function alone. Seed bought this turn cannot be sown until
        the next one (market resolves after unit actions), so the tile shortfall
        does not shrink when seed is bought — it shrinks when seed is PLANTED. A
        farm short two melon tiles therefore bought two more melon seeds every
        turn: measured, it held 11 melon and 55 wheat seeds by the end of day 0
        with 306 coins left of 3,000, and never made payroll again.

        Ordering still matters, because `_market` spends the budget as it walks
        this dict, so the crop listed first is the one funded when cash is short.
        """
        empty = len(jobs[PLANT])
        if empty <= 0:
            return {}
        targets: dict[str, int] = {}
        for short, crop in self._deficits(obs, {}):
            if empty <= 0:
                break
            want = min(short, empty)
            targets[crop] = want
            empty -= want
        return targets

    def _plant_action(self, obs: Obs, queued: dict[str, int],
                      seeds_left: dict[str, int]) -> str | None:
        """Sow whichever crop is furthest below its planned share and in stock."""
        for _short, crop in self._deficits(obs, queued):
            if seeds_left.get(crop, 0) > 0:
                return crop
        # Nothing planned is in stock. Rather than PASS, sow anything we hold —
        # a seed already bought is a sunk cost and an empty tile earns nothing.
        for crop in self._crops():
            if seeds_left.get(crop, 0) > 0:
                return crop
        return None

    # --- when to harvest ------------------------------------------------------

    @staticmethod
    def _ripe(obs: Obs, tile) -> bool:
        """One-time crops as the base does; ongoing crops held until full.

        `WheatFarm._ripe` is called explicitly rather than through `super()`
        because both are staticmethods and zero-argument `super()` has no
        instance to bind to inside one.
        """
        crop = CROPS.get(tile.get("crop"))
        if crop is None or not crop["ongoing"]:
            return WheatFarm._ripe(obs, tile)

        units = tile.get("yield_units", 0)
        if units <= 0:
            return False
        age = obs.day - int(tile.get("planted_day", obs.day))
        if age < crop["first_yield_day"]:
            return False  # HARVEST is a silent no-op here and wastes the turn
        if units >= crop["max_yield"] or obs.is_last_day:
            return True
        # The engine sets `max_lifespan_step` the moment the final production
        # lands, after which `_decay_plants` removes a unit every other step.
        # Past that point holding for a fuller load actively loses stock.
        last = (crop["first_yield_day"]
                + (crop["max_yield"] - 1) * max(1, crop["interval"]))
        return age >= last

    # --- how fast to sell -----------------------------------------------------

    def _sell_quantity(self, obs: Obs, item: str, qty: int) -> int:
        """Meter against an ABSOLUTE floor derived from the good's base price.

        `dump_capacity`, the older helper, measures against the price right now,
        which keeps feeding a market that has already collapsed — half of 14 is
        still "within the floor ratio". `depth_to_price` against
        `SELL_FLOOR_RATIO × base` says the thing we actually mean.
        """
        if qty <= 0:
            return 0
        # Unsold stock scores zero, so the last day is an unconditional dump.
        if obs.is_last_day:
            return qty
        inventory = obs.market_inventory.get(item, MARKET_I0)
        room = depth_to_price(item, inventory,
                              self.SELL_FLOOR_RATIO * base_price(item))
        return min(qty, max(room, self._relief(obs)))

    def _relief(self, obs: Obs) -> int:
        """Units that must go regardless of price, to stop the shed spilling.

        Discarded stock is worth zero, which is worse than any price on the
        curve, so some forced selling is right — but only the EXCESS. The first
        version returned the whole shelf the moment the shed passed
        `SHED_PRESSURE`, which quietly overrode the throttle it sits next to and
        crashed a market to free a handful of slots.
        """
        return max(0, int(obs.shed_used() - self.SHED_PRESSURE * SHED_CAPACITY))
