"""RanchFarm — a pure animal specialist. It grows nothing.

## Why it is pure

An earlier version of this file subclassed `MelonFarm`, so crops and animals
lived inside one strategy. The consequence was that "how do nine units split
between watering and hauling" became a hardcoded `PRIORITY` tuple, and **every
ordering cost ~6,600**:

    PICKUP above the crop jobs   -6,600, flat in herd size (3 sheep or 11)
    PICKUP below the crop jobs   the flock never formed at all — zero sheep,
                                 15,009 coins idle by day 12
    PICKUP only at dawn          the flock formed, and still lost 6,700

A penalty that does not vary with herd size is not a sizing problem. That was a
*decision* — how to divide labour between two domains — frozen into a constant.
Decisions belong to the controller, where they can read the observation, be
tuned by BO, and eventually be learned. So this file now does one thing.

**A strategy should be excellent at one domain. Combining domains is the
controller's job.**

## Feed is bought, not grown

A rancher needs one wheat per animal per day, and growing it would drag the
whole crop engine back in. So it buys:

    wheat price rises as we drain the market (buying pushes it UP):
        after   0 bought   25
        after 300 bought   42
        after 900 bought   55

    a sheep eats 1 wheat/day and makes 1.17 wool/day at ~200 = 234 coin/day
    so even at 55 a unit the margin is ~179 coin/day/sheep

Buying is not a compromise, it is the better trade — and it keeps this file
free of tiles, sowing and watering entirely.

## Why animals, and how many

Demand is a RATE. Shops consume every 4 turns, and a shop selling exactly one
product consumes at 2x. Measured season drain over 10 seeds with nobody selling:

    WOOL 289 @ 200 = 57,840     MILK 273 @ 160 = 43,680     EGG 273 @ 50 = 13,650

`YARN_STORE` sells only wool, so it drains at double rate — which is why wool is
the deepest market despite having the fewest shops. Steady-state output, verified
against `_daily_refresh_animals` (production every `interval` days is `1 + care
bonus accrued since the last`, bonus +1 per fed-and-cared day) and cross-checked
against a real episode:

    animal   unit/day   coin/day     to meet drain     (replay measured)
    COW          1.50        240     8 x 400            1.29
    SHEEP        1.17        234    11 x 500            1.17
    GOOSE        2.00        100     6 x 300

Sized to MEET the drain: past it the price falls and the marginal animal earns
pennies, exactly as melon did. Sheep-only, because a sheep-only herd measured
better than a mixed one even though milk has more shops.

`BUILD_PASTURE` / `BUILD_COOP` have **no cost check** in the engine — a structure
is one unit-turn and a tile. Only the animal costs coins.

## The supply chain, which is most of the code

Three engine facts force hauling:

1. `BUY_ANIMAL` and `BUY_PRODUCT` put goods in the **shed**, not on the board.
2. `PLACE` spends the animal from the **acting unit's inventory**, standing on a
   matching empty structure.
3. `FEED` spends 1 WHEAT from the **acting unit's inventory** — not the shed.

So everything routes through a unit walking to the shed and picking up. Hands
spawn ON the shed access tiles at hour 0, so a pickup in the first hours of the
day costs one turn and no travel; `HAUL_HOURS` confines it to that window.

## One quadrant

A rancher needs one tile per animal, not fifty. The NW quadrant alone holds 25,
so this never buys land — saving 1,000 coins and, more importantly, keeping every
animal within a few steps of the shed the feed comes from.
"""

from ..game.actions import PROCUREMENT, TurnPlan
from ..game.config import ANIMALS, SHED_CAPACITY
from ..game.observation import Obs
from .wheat_farm import CROP, WheatFarm

FEED, CARE, PLACE, PICKUP, BUILD, COLLECT, ANIMAL_HARVEST = (
    "FEED", "CARE", "PLACE", "PICKUP", "BUILD", "COLLECT_FERTILIZER", "AHARVEST",
)

#: Target herd, sized to the season drain: 289 wool / (1.17 per sheep-day x 22
#: productive days) ~= 11 sheep. Measured (as a hybrid, before this file was made
#: pure): 6 -> +17,168, 11 -> +19,735, 14 -> +17,434, 18 -> +17,482.
HERD = {"SHEEP": 11}

#: Days of feed to keep in the shed. Two unfed days and an animal ESCAPES, so
#: this buffer is survival, not convenience.
FEED_DAYS_BUFFER = 3

#: Wheat a unit carries per trip. Enough to feed several animals in one round.
FEED_CARRY = 8

#: Only haul in the first hours of the day, when hands are still standing on the
#: shed tiles they spawned on and a pickup is nearly free.
HAUL_HOURS = 3

#: Cash kept back from livestock purchases, so feed and wages always win the tie.
CASH_RESERVE_FOR_HERD = 900.0

#: Stop buying animals with fewer days left than this: a sheep needs 6 days to
#: first yield and the capital is otherwise a write-off.
HERD_LEAD_DAYS = 10


def _structure_for(animal: str) -> str:
    return ANIMALS[animal]["structure"]


class RanchFarm(WheatFarm):
    name = "ranch_farm"

    #: No crop jobs at all. This is the whole point of the file.
    PRIORITY = (FEED, PICKUP, PLACE, ANIMAL_HARVEST, CARE, COLLECT, BUILD)

    #: One quadrant is 25 tiles for at most 25 animals, and keeps them near the
    #: shed. Buying land would cost 1,000 and lengthen every feed round.
    MAX_QUADRANTS = 1

    #: Hands scale with the HERD, not with tiles — the base class scales with
    #: tiles, which for a rancher is meaningless.
    MIN_HANDS = 2
    MAX_HANDS = 6
    HANDS_PER_ANIMAL = 0.45

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
    def _vacant_structures(obs: Obs) -> int:
        return sum(1 for t in obs.owned_tiles()
                   if isinstance(t.raw, dict)
                   and t.raw.get("kind") in ("PASTURE", "COOP")
                   and "animal" not in t.raw)

    def _wanted(self, obs: Obs) -> dict[str, int]:
        """Animals still to acquire. Empty once the season is too short."""
        if obs.days_left < HERD_LEAD_DAYS:
            return {}
        have = self._animals(obs)
        shed = obs.shed
        return {a: n - have.get(a, 0) - int(shed.get(a, 0))
                for a, n in HERD.items()
                if n - have.get(a, 0) - int(shed.get(a, 0)) > 0}

    def _feed_wanted(self, obs: Obs) -> int:
        """Wheat to hold: enough to cover the herd for FEED_DAYS_BUFFER days."""
        n = sum(self._animals(obs).values()) + sum(
            int(obs.shed.get(a, 0)) for a in HERD)
        held = int(obs.shed.get(CROP, 0)) + sum(
            int((inv or {}).get(CROP, 0)) for inv in obs.inventories)
        return max(0, n * FEED_DAYS_BUFFER - held)

    # --- step 1: jobs ---------------------------------------------------------

    def _classify(self, obs: Obs) -> dict[str, list]:
        """Animal jobs only. No WATER, no PLANT, no HARVEST of crops, no DIG."""
        jobs: dict[str, list] = {k: [] for k in self.PRIORITY}

        carrying_animal = any(
            any(k in ANIMALS and v > 0 for k, v in (inv or {}).items())
            for inv in obs.inventories
        )
        for t in obs.owned_tiles():
            raw = t.raw
            if not isinstance(raw, dict):
                continue
            if "animal" in raw:
                spec = ANIMALS[raw["animal"]]
                if not raw.get("fed_today", False):
                    jobs[FEED].append(t.pos)
                elif raw.get("yield_units", 0) >= spec["max_held"] - 1 or obs.is_last_day:
                    jobs[ANIMAL_HARVEST].append(t.pos)
                elif not raw.get("cared_today", False):
                    jobs[CARE].append(t.pos)
                elif raw.get("fertilizer_available", False):
                    jobs[COLLECT].append(t.pos)
            elif raw.get("kind") in ("PASTURE", "COOP") and carrying_animal:
                jobs[PLACE].append(t.pos)

        # Build just in time, for livestock already bought and waiting. Building
        # ahead of the herd is how the first version ended up with 17 empty
        # pastures and a starved farm.
        homeless = sum(int(obs.shed.get(a, 0)) for a in HERD) + sum(
            int((inv or {}).get(a, 0)) for inv in obs.inventories for a in HERD)
        room = max(0, homeless - self._vacant_structures(obs))
        if room:
            empty = [t.pos for t in obs.owned_tiles() if t.empty]
            jobs[BUILD] = empty[:room]

        if obs.hour <= HAUL_HOURS and self._pickup_item(obs) is not None:
            jobs[PICKUP] = list(obs.shed_tiles)[:2]
        return jobs

    def _pickup_item(self, obs: Obs) -> str | None:
        """Animals first, then feed. Only when feed is actually short."""
        for animal in HERD:
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
            for a in self._wanted(obs):
                return (["BUILD_PASTURE"] if _structure_for(a) == "PASTURE"
                        else ["BUILD_COOP"])
            return ["BUILD_PASTURE"]
        if kind == PICKUP:
            item = self._pickup_item(obs)
            if item is None:
                return ["PASS"]
            return ["PICKUP", item, 1 if item in ANIMALS else FEED_CARRY]
        if kind == PLACE:
            invs = obs.inventories
            inv = (invs[unit_idx] if unit_idx < len(invs) else {}) or {}
            tile = obs.tile(*target)
            want = tile.get("kind") if tile is not None else None
            for a, n in inv.items():
                if a in ANIMALS and n > 0 and _structure_for(a) == want:
                    return ["PLACE", a]
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
        money = obs.money - CASH_RESERVE_FOR_HERD

        # Feed first, always. An animal is worth ~234 coins a day and costs a
        # wheat; being caught short for two days loses the animal outright.
        feed = self._feed_wanted(obs)
        if feed > 0 and money > 0:
            plan.order("BUY_PRODUCT", CROP, feed, priority=PROCUREMENT)
            return

        for animal, short in sorted(self._wanted(obs).items(),
                                    key=lambda kv: -ANIMALS[kv[0]]["cost"]):
            if short > 0 and money >= ANIMALS[animal]["cost"]:
                plan.order("BUY_ANIMAL", animal, 1, priority=PROCUREMENT)
                return  # one per turn; cash, not intent, is the constraint

    def _sell_quantity(self, obs: Obs, item: str, qty: int) -> int:
        """Sell produce; never sell livestock or the feed the herd needs."""
        if item in ANIMALS:
            return 0
        qty = super()._sell_quantity(obs, item, qty)
        if item == CROP and not obs.is_last_day:
            qty -= self._feed_wanted(obs) + int(obs.shed.get(CROP, 0))
        return qty
