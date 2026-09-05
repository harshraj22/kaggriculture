"""RanchFarm — a pure animal specialist, sized to the market's thirst.

It grows nothing. This docstring is the specification.

## Why it is pure

An earlier version subclassed `MelonFarm`, so crops and animals lived inside one
strategy. The consequence was that "how do nine units split between watering and
hauling" became a hardcoded `PRIORITY` tuple, and **every ordering cost ~6,600**:

    PICKUP above the crop jobs   -6,600, flat in herd size (3 sheep or 11)
    PICKUP below the crop jobs   the flock never formed at all — zero sheep,
                                 15,009 coins idle by day 12
    PICKUP only at dawn          the flock formed, and still lost 6,700

A penalty that does not vary with herd size is not a sizing problem. That was a
*decision* — how to divide labour between two domains — frozen into a constant.
Decisions belong to the controller, where they can read the observation, be tuned
by BO, and eventually be learned. **A strategy should be excellent at one domain.
Combining domains is the controller's job.**

## Why animals are where the money is

Measured by driving `_daily_refresh_animals` directly for 24 days, feeding every
day, harvesting on sight — and separately with `CARE` withheld:

    animal  product   u/day cared   u/day uncared   coins/day/TILE   cost
    SHEEP   WOOL             1.25            0.29              250    500
    COW     MILK             1.25            0.38              200    400
    GOOSE   EGG              1.83            0.88               92    300

A crop tile is worth 16-22 coins/day (see `market_farm.py`). **A sheep tile is
worth 250.** That single comparison is why this file matters more than any crop
tuning: WOOL + MILK + EGG are 130,164 of the 226,530 the whole season is worth.

Two consequences that are easy to get wrong, and both were:

* **CARE is worth 4.3× on sheep, 3.3× on cows.** The engine accrues a
  `pending_care_bonus` of +1 for every day an animal is both fed AND cared for,
  then spends the whole accrual on the next production day. Skipping care does
  not cost a little yield, it costs most of it. CARE is a daily obligation on
  par with feeding.
* **Harvest on sight, not when nearly full.** Steady-state production is
  `min(max_held, 1 + interval)` units every `interval` days — 4 units per 3 days
  for a sheep, against a `max_held` of 6. The previous rule here waited for
  `yield_units >= max_held - 1`, which means waiting for a second production that
  then overflows the cap. Measured over 24 days: harvesting on sight yields **30
  units**, every 4 days yields 20, every 6 days yields 18. The old rule was
  throwing away a third of the flock's output.

## How many animals

Same rule as `market_farm`, and for the same reason: a good's price only stays
near `base` while supply matches what the town drinks, so the drain sizes the
herd. Output rate matches drain rate at

    herd(animal) = projected_drain_per_day(product) / units_per_day(animal)

which with the full eight shop instances is ~10 sheep, ~13 cows, ~7 geese. The
old `HERD = {"SHEEP": 11}` was hand-set from a wool-only reading, and it was very
nearly right for wool alone — it just left milk and egg, another 56,000 of
addressable market, completely untouched.

Ranked by coins per tile-day (SHEEP 267 > COW 240 > GOOSE 100) and filled greedily
against the tile budget, exactly like the crop plan.

## Metering matters more here than for crops

The animal products carry the harshest curves in the game. Wool is `sq/3.2` with
`T=105`:

    +0 above I0 -> 200      +20 -> 177      +40 -> 107      +59 -> 1 (floor)

**Fifty-nine units of wool, sold in one go, takes the price from 200 to 1.** Milk
is `linear/1.6` and reaches the floor at 76. A day's flock output is roughly the
drain, so selling steadily is fine and selling a backlog is ruinous — which is
exactly the mistake `melon_farm` made with melon. Sales are throttled with
`depth_to_price` against `SELL_FLOOR_RATIO × base`.

Fertilizer is the special case: **its drain is exactly zero**, because no shop
and not the town centre consumes it. Every unit sold sits in the market forever.
Its curve is gentle (`linear/0.4`, floor at 500 units) so it is worth selling, but
nothing ever clears it and the throttle is the only thing standing between a
season of free byproduct and a market at 1.

## Feed is bought, not grown

A rancher needs one wheat per animal per day, and growing it would drag the whole
crop engine back in. Buying pushes the wheat price UP (buying drains market
inventory, and below `I0` the price rises): 25 at the start, ~42 after 300 bought,
~55 after 900. Even at 55 a sheep returns ~250/day on one wheat, so buying is not
a compromise, it is the better trade.

It is also the clearest case for the controller: run under `AllocateController`
alongside `market_farm` and the crop side's wheat lands in the **same shed** the
ranch feeds from. Neither strategy needs to know about the other.

## The supply chain, which is most of the code

Three engine facts force hauling:

1. `BUY_ANIMAL` and `BUY_PRODUCT` put goods in the **shed**, not on the board.
2. `PLACE` spends the animal from the **acting unit's inventory**, standing on a
   matching empty structure.
3. `FEED` spends 1 WHEAT from the **acting unit's inventory** — not the shed.

So everything routes through a unit walking to the shed and picking up. Hands
spawn ON the shed access tiles at hour 0, so the first pickup of the day costs
one turn and no travel — and it is needed every day regardless, because the
evening reset wipes `private["inventories"]` along with the hands. `PICKUP` has
no quantity cap, so the trip is the cost and the load is free.

`BUILD_PASTURE` / `BUILD_COOP` have **no cost check** in the engine — a structure
is one unit-turn and a tile. Only the animal costs coins. Building is still done
just-in-time, for livestock already bought and waiting: the first version of this
file built 17 empty pastures and starved a day-0 cow, and scored 809.

## Status

Against `opponents/v48`, 12 seeds x both seats, untuned:

    ranch_farm (this)   16,983   sd 10,960
    ranch_farm (before)  2,604              <- 6 seeds; its 32,804 was vs a weak opponent
    market_farm         26,743
    melon_farm          31,935

A 6.5x improvement on what it replaces and still behind the crop strategies, with
a standard deviation two thirds of its mean. The variance is the honest problem:
the good episodes reach 45,600 and the bad ones lose the herd. Every collapse
traced so far has been starvation — an animal that misses two meals escapes and
takes its capital with it — which is why `MAX_HANDS` is the largest lever here
and why the search range for it runs to 20.

The constants are starting points, not measured optima;
`tools/optimize.py --space ranch_farm` searches them through the `params:`
channel. See `notes/ranch_farm.md` for the full table and the search to run.
"""

from ..game.actions import PROCUREMENT, TurnPlan
from ..game.config import ANIMALS, MARKET_I0, SHED_CAPACITY
from ..game.market import base_price, depth_to_price, projected_drain_per_day
from ..game.observation import Obs
from .wheat_farm import CROP, WheatFarm

FEED, CARE, PLACE, PICKUP, BUILD, COLLECT, ANIMAL_HARVEST = (
    "FEED", "CARE", "PLACE", "PICKUP", "BUILD", "COLLECT_FERTILIZER", "AHARVEST",
)

#: Animals the ranch may hold. Ranked and sized at runtime, not fixed here.
#:
#: GOOSE is deliberately excluded by default and left available to a search. Per
#: unit-turn it earns 33 against a sheep's 114, and measured against v48 (6 seeds,
#: both seats) every herd containing geese was worse:
#:
#:     SHEEP only          24,511   sd 14,407
#:     SHEEP + COW         23,330   sd 10,234   <- default: same mean, less spread
#:     SHEEP + COW + GOOSE  5,679   sd  7,218
#:
#: Sheep and cow are a coin flip on the mean, so the pair is chosen for the
#: variance: the ladder is Bradley-Terry over win/loss, where a wider spread at
#: the same mean is rating-NEGATIVE. It also leaves milk's 44,544 addressable.
HERD_MIX = ("SHEEP", "COW")

#: Multiplier on each animal's drain-matching count, as `SATURATION` is for crops.
#: Measured: 1.0 -> 23,330, 1.6 -> 11,309. Overshooting is worse here than for
#: crops because wool's curve is `sq/3.2` — the surplus does not fetch a lower
#: price, it fetches nothing.
HERD_SATURATION = 1.0

#: Floor for metered selling, as a fraction of the product's BASE price. Tighter
#: than the crop default because wool and milk have the steepest curves in the
#: game — 59 units of wool takes it from 200 to the 1-coin floor.
SELL_FLOOR_RATIO = 0.75

#: Fraction of shed capacity above which the sell throttle is ignored. The shed
#: caps at 100 and the end-of-day drop DISCARDS the overflow, which is worse than
#: any price on the curve — and a ranch competes for that space with its own feed.
SHED_PRESSURE = 0.75

#: Days of feed to keep. Two unfed days and an animal ESCAPES, so this is
#: survival, not convenience. Bounded by the shed below: a 30-animal herd at 3
#: days is 90 wheat, which would leave no room for the produce being sold.
FEED_DAYS_BUFFER = 3

#: Share of the shed feed may occupy, so produce always has somewhere to land.
FEED_SHED_SHARE = 0.5

#: Wheat a unit carries per trip. `PICKUP` has no quantity cap in the engine, and
#: unit inventories are wiped every evening, so this wants to be a unit's whole
#: day of feeding in one trip — the cost is the walk, not the load.
FEED_CARRY = 12

#: Cash kept back from livestock purchases, so feed and wages always win the tie.
CASH_RESERVE_FOR_HERD = 900.0

#: Stop buying an animal with fewer days left than its `first_yield_day` plus
#: this. A cow needs 8 days before its first milk; bought later the 400 coins are
#: a write-off. Per-animal, because those lead times differ by a factor of two.
HERD_LEAD_SLACK = 3


def animal_profile(animal: str) -> tuple[float, int]:
    """`(units per day at steady state, first_yield_day)`, from `ANIMALS`.

    The engine produces `1 + pending_care_bonus` units every `interval` days and
    the bonus accrues +1 per fed-and-cared day, so a fully tended animal produces
    `min(max_held, 1 + interval)` per production. Verified against
    `_daily_refresh_animals` over 24 days: sheep 30 units, cow 30, goose 44.
    """
    spec = ANIMALS[animal]
    interval = max(1, spec["interval"])
    per_production = min(spec["max_held"], 1 + interval)
    return per_production / interval, spec["first_yield_day"]


def unit_turns_per_day(animal: str) -> float:
    """Unit-turns one animal costs per day: FEED + CARE + a share of a HARVEST.

    Feeding and caring are both daily and both mandatory in practice — an unfed
    animal escapes and an uncared one produces a quarter as much — so the only
    thing that varies between species is how often the produce must be picked up,
    which is once per `interval` days.
    """
    return 2.0 + 1.0 / max(1, ANIMALS[animal]["interval"])


def _structure_for(animal: str) -> str:
    return ANIMALS[animal]["structure"]


class RanchFarm(WheatFarm):
    name = "ranch_farm"

    #: No crop jobs at all. This is the whole point of the file.
    #:
    #: **PICKUP must sit directly under FEED**, and it is tempting to put CARE
    #: there instead: one CARE turn is worth ~0.96 extra units/day on a sheep
    #: (~190 coins) against a COLLECT's single fertilizer. Measured, promoting
    #: CARE above PICKUP killed the herd — 13 animals on day 22, **1 on day 23**,
    #: and 4,627 final. `FEED` spends wheat from the acting unit's inventory, so a
    #: unit holding none cannot feed; it falls through to CARE, which it *can*
    #: do, and is consumed there. Nobody hauls, every animal misses two days, and
    #: `consecutive_unfed >= 2` escapes the lot.
    #:
    #: The lesson generalises past this file: a job that ENABLES the survival job
    #: outranks any job that merely improves yield, however large the yield.
    PRIORITY = (FEED, PICKUP, PLACE, ANIMAL_HARVEST, CARE, COLLECT, BUILD)

    HERD_MIX = HERD_MIX
    HERD_SATURATION = HERD_SATURATION
    SELL_FLOOR_RATIO = SELL_FLOOR_RATIO
    SHED_PRESSURE = SHED_PRESSURE
    FEED_DAYS_BUFFER = FEED_DAYS_BUFFER
    FEED_SHED_SHARE = FEED_SHED_SHARE
    FEED_CARRY = FEED_CARRY
    CASH_RESERVE_FOR_HERD = CASH_RESERVE_FOR_HERD
    HERD_LEAD_SLACK = HERD_LEAD_SLACK

    #: One quadrant. A drain-sized herd across all three products would want ~30
    #: tiles and one quadrant holds 25, so a second looks tempting — but measured
    #: it LOSES: 9,530 on one quadrant against 5,858 on two. The 1,000 coins buys
    #: land the herd is too labour-limited to fill, out of the same purse the
    #: animals come from, and every animal ends further from the shed the feed
    #: is hauled from.
    MAX_QUADRANTS = 1

    #: Animals one unit can keep fed, cared for and picked up. Each costs ~2.4
    #: unit-turns a day and a unit completes roughly ten jobs a day after travel,
    #: so ~4 is the ceiling; below it, animals miss meals and escape.
    ANIMALS_PER_UNIT = 3.5

    #: Hands scale with the HERD, not with tiles — the base class scales with
    #: tiles, which for a rancher is meaningless.
    #:
    #: These are far above the crop farm's 8, and they are the single largest
    #: lever measured on this strategy: 0.45/8 scored 4,662 and 0.80/14 scored
    #: 23,330 on the same herd. What the extra hands buy is not scale, it is
    #: RELIABILITY — an animal that misses two meals escapes and takes its
    #: capital with it, so a crew sized to the average day loses the herd on the
    #: bad ones. `wheat_farm` measured more hands as strictly worse because its
    #: failure mode is travel; a ranch's failure mode is starvation, and they
    #: pull in opposite directions.
    MIN_HANDS = 2
    MAX_HANDS = 14
    HANDS_PER_ANIMAL = 0.8

    def _hand_target(self, obs: Obs, owned: int) -> int:
        n = sum(self._animals(obs).values()) + self._vacant_structures(obs)
        return max(self.MIN_HANDS, min(self.MAX_HANDS, int(n * self.HANDS_PER_ANIMAL)))

    # --- reading the board ----------------------------------------------------

    @staticmethod
    def _animals(obs: Obs) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in obs.owned_tiles():
            raw = t.raw
            if isinstance(raw, dict) and "animal" in raw:
                out[raw["animal"]] = out.get(raw["animal"], 0) + 1
        return out

    @staticmethod
    def _vacant_by_kind(obs: Obs) -> dict[str, list]:
        """Empty PASTUREs and COOPs, keyed by kind.

        Keyed, not counted, because **a pasture is no use to a goose**. Treating
        the two as interchangeable stranded a goose in the shed for eight days
        while sixteen empty pastures sat on the board: every one of them was
        emitted as a `PLACE` target, the nearest unit was sent to one each turn,
        and `_action_for` found no matching structure and returned PASS. Those
        wasted assignments also outranked CARE and HARVEST, so the herd stopped
        being tended and starved — 1,424 final, from 2 sheep and a cow.
        """
        out: dict[str, list] = {"PASTURE": [], "COOP": []}
        for t in obs.owned_tiles():
            raw = t.raw
            if (isinstance(raw, dict) and raw.get("kind") in out
                    and "animal" not in raw):
                out[raw["kind"]].append(t.pos)
        return out

    def _vacant_structures(self, obs: Obs) -> int:
        return sum(len(v) for v in self._vacant_by_kind(obs).values())

    def _homeless(self, obs: Obs) -> dict[str, int]:
        """Structures still to build, by kind, for livestock already bought."""
        vacant = self._vacant_by_kind(obs)
        need: dict[str, int] = {}
        for animal, n in self._in_transit(obs).items():
            kind = _structure_for(animal)
            need[kind] = need.get(kind, 0) + n
        return {k: v - len(vacant.get(k, ())) for k, v in need.items()
                if v > len(vacant.get(k, ()))}

    # --- how many animals, of which kind --------------------------------------

    def _herd_plan(self, obs: Obs) -> dict[str, int]:
        """Target head count per animal, from the town's thirst.

        Rate-matched, so no horizon appears: we want output per day to equal
        drain per day, and both sides of `drain / units_per_day` are rates.

        **Ranked by coins per unit-turn, not per tile.** A ranch has tiles to
        spare — one quadrant is 25 of them for a herd of ten — and no labour to
        spare, since every animal wants feeding and caring every single day. Per
        tile the three are 267 / 240 / 100 and look close; per unit-turn they are

            SHEEP 114     COW 96     GOOSE 33

        because a goose must be picked up daily where a sheep is picked up every
        third day. Measured against v48, and the gap is not subtle: a herd ranked
        per tile scored **2,434** and a sheep-only herd **9,530**, because
        capital and labour spent on a goose is capital and labour not spent on
        the animal that earns three times as much for it.

        The budget is therefore labour as well as land, and ranking by the scarce
        resource is the same correction `market_farm._score` makes for cash.
        """
        tiles = sum(1 for t in obs.owned_tiles() if not t.locked)
        labour = (1 + len(obs.hands)) * self.ANIMALS_PER_UNIT
        budget = int(min(tiles, labour))
        if budget <= 0:
            return {}

        ranked = []
        for animal in self.HERD_MIX:
            spec = ANIMALS.get(animal)
            if spec is None:
                continue
            rate, lead = animal_profile(animal)
            product = spec["product"]
            value = rate * self._price(obs, product) / unit_turns_per_day(animal)
            drain = projected_drain_per_day(product, obs.unlocked_shops, obs.days_left)
            want = round(self.HERD_SATURATION * drain / rate)
            if want > 0 and value > 0:
                ranked.append((value, animal, want, lead))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        plan: dict[str, int] = {}
        for _value, animal, want, _lead in ranked:
            if budget <= 0:
                break
            take = min(want, budget)
            plan[animal] = take
            budget -= take
        return plan

    def _price(self, obs: Obs, product: str) -> float:
        px = obs.prices.get(product)
        return float(px) if px else base_price(product)

    def _wanted(self, obs: Obs) -> dict[str, int]:
        """Animals still to acquire, netting off any already bought and in transit.

        Gated per animal rather than globally: a goose yields on day 4 and a cow
        on day 8, so a single cutoff either wastes the last week of goose income
        or buys cows that never produce.
        """
        have = self._animals(obs)
        in_transit = self._in_transit(obs)
        out = {}
        for animal, target in self._herd_plan(obs).items():
            _rate, lead = animal_profile(animal)
            if obs.days_left < lead + self.HERD_LEAD_SLACK:
                continue
            short = target - have.get(animal, 0) - in_transit.get(animal, 0)
            if short > 0:
                out[animal] = short
        return out

    @staticmethod
    def _in_transit(obs: Obs) -> dict[str, int]:
        """Livestock bought but not yet standing on a structure — in the shed or
        carried by a unit. Missing this double-buys the whole herd."""
        out: dict[str, int] = {}
        for animal in ANIMALS:
            n = int(obs.shed.get(animal, 0)) + sum(
                int((inv or {}).get(animal, 0)) for inv in obs.inventories)
            if n:
                out[animal] = n
        return out

    def _feed_wanted(self, obs: Obs) -> int:
        """Wheat to hold: the herd's needs for the next few days.

        Three separate caps, each of which was needed:

        * `FEED_DAYS_BUFFER`, because two unfed days lose the animal.
        * `FEED_SHED_SHARE` of the shed, because a thirty-animal herd at three
          days is ninety wheat of a hundred-unit shed, which starves the produce
          the feed exists to protect — the end-of-day drop discards the overflow.
        * `days_left`, and **zero on the last day**. Without it the last day is a
          churn loop: `_sell_quantity` dumps the whole shed (unsold stock scores
          nothing), `_feed_wanted` immediately sees an empty barn and rebuys, and
          the pair runs 24 times paying the buy/sell spread each round. Measured:
          695 wheat bought and 548 sold in one episode, on a herd of four.
        """
        if obs.is_last_day:
            return 0
        mouths = sum(self._animals(obs).values()) + sum(self._in_transit(obs).values())
        held = int(obs.shed.get(CROP, 0)) + sum(
            int((inv or {}).get(CROP, 0)) for inv in obs.inventories)
        days = min(self.FEED_DAYS_BUFFER, max(1, obs.days_left))
        target = min(mouths * days, int(SHED_CAPACITY * self.FEED_SHED_SHARE))
        return max(0, target - held)

    # --- step 1: jobs ---------------------------------------------------------

    def _classify(self, obs: Obs) -> dict[str, list]:
        """Animal jobs only. No WATER, no PLANT, no crop HARVEST, no DIG."""
        jobs: dict[str, list] = {k: [] for k in self.PRIORITY}

        for t in obs.owned_tiles():
            raw = t.raw
            if not isinstance(raw, dict) or "animal" not in raw:
                continue
            if not raw.get("fed_today", False):
                jobs[FEED].append(t.pos)
            elif self._ready(obs, raw):
                jobs[ANIMAL_HARVEST].append(t.pos)
            elif not raw.get("cared_today", False):
                jobs[CARE].append(t.pos)
            elif raw.get("fertilizer_available", False):
                jobs[COLLECT].append(t.pos)

        # Only structures a CARRIED animal can actually move into. Emitting every
        # vacant structure sends units to homes of the wrong kind, where they
        # PASS — and those assignments outrank CARE and HARVEST.
        vacant = self._vacant_by_kind(obs)
        for kind in self._carried_structures(obs):
            jobs[PLACE].extend(vacant.get(kind, ()))

        # Build just in time, for livestock already bought and waiting. Building
        # ahead of the herd is how the first version ended up with 17 empty
        # pastures, a starved day-0 cow and a score of 809.
        room = sum(self._homeless(obs).values())
        if room:
            jobs[BUILD] = [t.pos for t in obs.owned_tiles() if t.empty][:room]

        jobs[PICKUP] = self._haul_targets(obs)
        return jobs

    def _haul_targets(self, obs: Obs) -> list:
        """Shed tiles to send units to, scaled to what the herd is short of.

        The hour gate this replaces (`hour <= HAUL_HOURS`, two tiles) was written
        for the hybrid version, where hauling competed with crop jobs and had to
        be confined to the free window at dawn. In a pure ranch there are no crop
        jobs to protect, and confining it is what starved the herd: unit
        inventories are **wiped every evening** along with the hands, so every
        feeding unit needs a fresh pickup every single day, and if the window is
        too narrow some animals simply never eat.

        `PICKUP` has no quantity cap in the engine, so one trip can carry a
        unit's whole day of feeding — the cost is the trip, not the load.
        """
        item = self._pickup_item(obs)
        if item is None:
            return []
        tiles = list(obs.shed_tiles)
        if item in ANIMALS:
            return tiles[:1]  # one animal moves at a time; PLACE follows

        unfed = sum(1 for t in obs.owned_tiles()
                    if isinstance(t.raw, dict) and "animal" in t.raw
                    and not t.raw.get("fed_today", False))
        carried = sum(int((inv or {}).get(CROP, 0)) for inv in obs.inventories)
        short = unfed - carried
        if short <= 0:
            return []
        carriers = min(len(tiles), 1 + (short - 1) // max(1, self.FEED_CARRY))
        return tiles[:carriers]

    @staticmethod
    def _ready(obs: Obs, raw: dict) -> bool:
        """Harvest as soon as waiting would overflow `max_held`.

        Production is all-or-nothing at `min(max_held, 1 + interval)` units on a
        production day, so an animal sitting on one production's worth is already
        at risk: the next one caps and the excess is simply lost. The rule this
        replaces waited for `yield_units >= max_held - 1`, which guarantees that
        overflow — measured, it collected 18-20 units per sheep over 24 days
        against 30 for harvesting on sight.
        """
        units = raw.get("yield_units", 0)
        if units <= 0:
            return False
        if obs.is_last_day:
            return True
        spec = ANIMALS.get(raw.get("animal"))
        if spec is None:
            return True
        per_production = min(spec["max_held"], 1 + max(1, spec["interval"]))
        return units + per_production > spec["max_held"]

    @staticmethod
    def _carried_structures(obs: Obs) -> set[str]:
        """Structure kinds some unit is currently carrying an animal for."""
        return {_structure_for(item)
                for inv in obs.inventories
                for item, n in (inv or {}).items()
                if item in ANIMALS and n > 0}

    def _pickup_item(self, obs: Obs) -> str | None:
        """Animals first, then feed. Only when feed is actually short."""
        for animal in self.HERD_MIX:
            if int(obs.shed.get(animal, 0)) > 0:
                return animal
        if int(obs.shed.get(CROP, 0)) <= 0:
            return None
        carried = sum(int((inv or {}).get(CROP, 0)) for inv in obs.inventories)
        unfed = sum(1 for t in obs.owned_tiles()
                    if isinstance(t.raw, dict) and "animal" in t.raw
                    and not t.raw.get("fed_today", False))
        return CROP if carried < unfed else None

    # --- who may take which job ----------------------------------------------

    def _can_do(self, kind: str, obs: Obs, unit_idx: int) -> bool:
        if kind not in (FEED, PLACE):
            return True
        invs = obs.inventories
        inv = (invs[unit_idx] if unit_idx < len(invs) else {}) or {}
        if kind == FEED:
            return int(inv.get(CROP, 0)) > 0
        return any(k in ANIMALS and v > 0 for k, v in inv.items())

    def _action_for(self, kind: str, obs: Obs, unit_idx: int, target) -> list:
        if kind == ANIMAL_HARVEST:
            return ["HARVEST"]
        if kind == BUILD:
            # The structure the waiting livestock actually needs, largest
            # shortfall first. A pasture is no use to a goose.
            homeless = self._homeless(obs)
            if homeless:
                kind_needed = max(homeless, key=lambda k: (homeless[k], k))
                return ["BUILD_PASTURE"] if kind_needed == "PASTURE" else ["BUILD_COOP"]
            return ["BUILD_PASTURE"]
        if kind == PICKUP:
            item = self._pickup_item(obs)
            if item is None:
                return ["PASS"]
            return ["PICKUP", item, 1 if item in ANIMALS else self.FEED_CARRY]
        if kind == PLACE:
            invs = obs.inventories
            inv = (invs[unit_idx] if unit_idx < len(invs) else {}) or {}
            tile = obs.tile(*target)
            want = tile.get("kind") if tile is not None else None
            for animal, n in inv.items():
                if animal in ANIMALS and n > 0 and _structure_for(animal) == want:
                    return ["PLACE", animal]
            return ["PASS"]
        return [kind]

    # --- step 2: market -------------------------------------------------------

    def _seed_targets(self, obs: Obs, jobs: dict) -> dict[str, int]:
        """No seed. A rancher sows nothing."""
        return {}

    def _market(self, obs: Obs, plan: TurnPlan, jobs: dict) -> None:
        super()._market(obs, plan, jobs)
        if obs.shed_used() >= SHED_CAPACITY - 2:
            return
        money = obs.money - self.CASH_RESERVE_FOR_HERD

        # Feed first, always. An animal returns 200-267 coins a day on one wheat,
        # and being caught short for two days loses the animal outright.
        feed = self._feed_wanted(obs)
        if feed > 0 and money > 0:
            plan.order("BUY_PRODUCT", CROP, feed, priority=PROCUREMENT)
            return

        # One animal per turn: cash, not intent, is the constraint, and the herd
        # is built out of realised profit. Most expensive first, because that is
        # also the most valuable per tile — see the table in the docstring.
        for animal in sorted(self._wanted(obs), key=lambda a: -ANIMALS[a]["cost"]):
            if money >= ANIMALS[animal]["cost"]:
                plan.order("BUY_ANIMAL", animal, 1, priority=PROCUREMENT)
                return

    def _sell_quantity(self, obs: Obs, item: str, qty: int) -> int:
        """Meter every product against an absolute floor; never sell livestock.

        Wool and milk carry the steepest curves in the game — 59 units of wool in
        one order takes the price from 200 to 1 — and fertilizer has a drain of
        exactly zero, so nothing the town does ever repairs an overshoot.
        """
        if qty <= 0 or item in ANIMALS:
            return 0

        # Feed is an input, not produce. Hold back what the herd needs, and only
        # then sell any surplus.
        if item == CROP and not obs.is_last_day:
            qty -= self._feed_wanted(obs) + int(obs.shed.get(CROP, 0))
            if qty <= 0:
                return 0

        if obs.is_last_day:
            return qty
        inventory = obs.market_inventory.get(item, MARKET_I0)
        room = depth_to_price(item, inventory,
                              self.SELL_FLOOR_RATIO * base_price(item))
        return min(qty, max(room, self._relief(obs)))

    def _relief(self, obs: Obs) -> int:
        """Units that must go regardless of price, to keep the shed from spilling.

        Only the EXCESS, not the whole shelf. Dumping everything the moment the
        shed passed 75% took wool from 200 to the 1-coin floor to free five slots
        — the throttle exists precisely to stop that, and the pressure valve was
        quietly overriding it. Overflow is discarded at the end of day, so zero is
        the alternative price and some forced selling is right; it is the size of
        it that was wrong.
        """
        limit = self.SHED_PRESSURE * SHED_CAPACITY
        return max(0, int(obs.shed_used() - limit))
