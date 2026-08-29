"""RanchFarm — animals, sized to what the shops actually consume.

⚠️ **NEEDS IMPROVEMENT — do not submit this.** With a flock actually running it
scores +28,516 against `melon_farm`'s +35,246 on v1/train: the herd costs ~6,700.
It is kept because the mechanism is sound and two of its derived predictions were
confirmed by measurement, and because the failure is diagnosed rather than
mysterious — see "Result" and "The real blocker is routing" at the end.
`melon_farm` remains the active submission.

This docstring is the specification. `RanchFarm` inherits the per-turn engine of
`WheatFarm` (tile classification, task-centric assignment, hiring, land, greedy
movement) and adds one thing: a herd, with the hauling that animals require.

Every number here is derived from `ANIMALS` / `MARKET_PARAMS` / `SHOPS` and from
`pass`-vs-`pass` measurement. None of it is copied from an opponent's play.

## Why animals

**Demand is a rate, not a pool.** Shops consume every 4 turns, and a shop selling
exactly ONE product consumes at 2x. Season drain, measured over 10 seeds in a
game where nobody sells:

    WOOL 289 @ 200 = 57,840     WHEAT 559 @  25 = 13,980
    STRAW 406 @ 120 = 48,744     EGG  273 @  50 = 13,650
    MILK  273 @ 160 = 43,680     MELON  30 @ 250 =  7,500

Melon absorbs **30 units a season**; `melon_farm` sold 110 and drove the price
from 266 to 62. Wool is the single most valuable market despite having the fewest
shops, because `YARN_STORE` sells only wool and therefore drains at double rate.

**Animals convert labour ~7x better than wheat.** From `_daily_refresh_animals`:
production every `interval` days is `1 + care bonus accrued since the last one`,
bonus +1 per fed-and-cared day.

    animal   unit/day   coin/day   coin/turn      to meet drain
    COW          1.50        240         107       8 x 400
    SHEEP        1.17        234         104      11 x 500
    GOOSE        2.00        100          44       6 x 300
    wheat           -          -          14

`BUILD_PASTURE` / `BUILD_COOP` have **no cost check** in the engine — a structure
is one unit-turn and a tile. Only the animal costs coins.

## The hauling problem, which is most of the code

Animals cannot be run from the tile alone. Three engine facts force a supply
chain:

1. `BUY_ANIMAL` puts the animal in the **shed**, not on the board.
2. `PLACE` spends the animal from the **acting unit's inventory**, and the unit
   must stand on a matching empty structure.
3. `FEED` spends 1 WHEAT from the **acting unit's inventory** — not the shed.

So: buy -> a unit walks to the shed and `PICKUP`s -> carries -> `PLACE` or
`FEED`. `_can_do` exists for this: a job that needs something in hand may only be
assigned to a unit already holding it, or the nearest unit wins a job it cannot
perform and the turn is spent walking to a no-op.

## Priority

    FEED > WATER > PLACE > CARE > COLLECT > HARVEST > PICKUP > BUILD > PLANT > DIG

`FEED` outranks everything: two unfed days and the animal **escapes**, losing the
capital outright. Watering is the same survival argument for plants. `PLACE`
outranks `CARE` because an animal in the shed earns nothing. `CARE` beats
harvesting because the bonus compounds into every later production.

## Feed: grow or buy

The herd eats one wheat per animal per day. At 25 animals over ~22 productive
days that is ~550 wheat, against a season wheat drain of only 559 — so we should
be feeding our wheat to animals rather than selling it into a market we already
glut. `_sell` therefore reserves `FEED_DAYS_BUFFER` days of feed before selling
any wheat, and buying the shortfall is left as a tunable.

## Sizing

Sized to MEET the drain, not to fill the farm: past the drain the price falls and
the marginal animal earns pennies, exactly as melon did.

## Result: the herd cannot pay for itself yet, and we know why

`RanchFarm` now subclasses `MelonFarm`, so it runs the melon cash engine AND a
flock. Measured on v1/train, 60 paired episodes:

    melon_farm (no herd)                    +35,246   sd   711
    ranch_farm, herd never forms            +35,129   sd   793
    ranch_farm, herd forms (haul at dawn)   +28,516   sd 2,272
    ranch_farm, wheat-funded (old)          +19,735   sd 4,176

**Whenever the flock actually exists it costs ~6,700.** That is the finding.

Three orderings were tried and the middle two are instructive:

1. `PICKUP` above the crop jobs — cost ~6,600 *whatever the herd size*, 3 sheep
   or 11. A penalty flat in herd size is the signature of a structural problem,
   not a sizing one: hauling was starving the crop cycle that funds everything.
2. `PICKUP` below the crop jobs — with 40+ crop jobs and 9 units it never won a
   unit at all. **Zero sheep all season, 15,009 coins idle by day 12**, and the
   score returned to `melon_farm`'s because there was effectively no herd.
3. `PICKUP` high but only in the first `HAUL_HOURS` of the day. Hands spawn ON
   the shed access tiles at hour 0, so a dawn pickup costs one turn and no
   travel. The flock forms (1 sheep by day 10, 10 by day 20) — and still loses
   6,700.

## The real blocker is routing, not animals

The per-turn arithmetic says a sheep earns ~104 coins per unit-turn against
wheat's ~14. It does not pay here because **62-66% of our unit-turns are spent
walking**, so the marginal unit-turn is worth far less than the average one, and
a herd needs its turns at specific times and places: FEED and PLACE spend from
the acting unit's inventory, so the wheat has to be carried to each animal.

The trace also shows the flock shrinking from 10 on day 20 to 6 on day 25 —
animals escaping after two missed feeds. We are not only failing to profit from
them, we are losing the capital.

So the order of work is: **fix routing first, then animals become affordable.**
Adding a supply chain to a movement layer this inefficient is what fails, not the
economics of livestock. Nothing here contradicts the market analysis above — wool
really is the deepest market, and a sheep really does out-earn a wheat tile per
turn. We simply cannot deliver the turns.
"""

from ..game.actions import PROCUREMENT, TurnPlan
from ..game.config import ANIMALS, SHED_CAPACITY
from ..game.observation import Obs
from .melon_farm import MelonFarm
from .wheat_farm import CROP, DIG, HARVEST, PLANT, WATER

FEED, CARE, PLACE, PICKUP, BUILD, COLLECT, ANIMAL_HARVEST = (
    "FEED", "CARE", "PLACE", "PICKUP", "BUILD", "COLLECT_FERTILIZER", "AHARVEST",
)

#: Target herd. Derived from season drain / steady-state output:
#: 289 wool / (1.17 wool per sheep-day x 22 productive days) ~= 11 sheep.
#:
#: Measured on v1/train, 60 paired episodes:
#:
#:     SHEEP 6                 +17,168     under-supplies the drain
#:     SHEEP 11                +19,735  <- the derived size, and the peak
#:     SHEEP 14                +17,434     past the drain; wool price falls
#:     SHEEP 18                +17,482
#:     SHEEP 11 + COW 8        +17,842     mixed herd, and sd jumps to 6,538
#:
#: Two derived claims held. Wool really is the best market (a sheep-only herd
#: beats the mixed one), and sizing to the DRAIN really is the right rule — 14
#: and 18 sheep both earn less than 11, because the extra wool arrives in a
#: market that already has enough.
HERD = {"SHEEP": 11}

#: Days of feed to hold back from sale before selling any wheat.
FEED_DAYS_BUFFER = 2

#: Hours after dawn during which hauling is allowed. Hands spawn on the shed
#: access tiles, so a pickup in this window is nearly free.
HAUL_HOURS = 2

#: Wheat a unit picks up per trip to the shed. Larger means fewer trips; too
#: large and one unit hoards the feed while animals elsewhere starve.
FEED_CARRY = 6

#: Cash that must remain AFTER buying an animal.
#:
#: The herd is funded out of realised profit, not out of the opening purse. The
#: first version bought a cow on day 0, had no wheat to feed it, and the animal
#: escaped after two unfed days — capital gone, and the farm never recovered
#: (score 809, worse than standing still). Wheat pays from day 2; a cow pays from
#: day 8. The crop economy has to be running first.
CASH_RESERVE_FOR_HERD = 1200.0

#: Stop buying animals with fewer days left than this.
#:
#: Not the full payback period — the marginal test is whether the animal returns
#: more than it cost, not whether it reaches steady state. A sheep bought with 8
#: days left yields from day 6 and produces twice before the season ends: ~6 wool
#: at ~200 against a 500 cost. At 12 the farm sat on 18,755 unspent coins from
#: day 18 onward, which is capital earning nothing.
HERD_LEAD_DAYS = 7

#: Wheat that must be on hand before the first animal is bought.
#:
#: An animal unfed for two days ESCAPES and the capital is gone. The first
#: version bought a sheep on day 0, when the farm had no wheat at all and none
#: growing, and lost it by day 2.
FEED_ON_HAND_BEFORE_BUYING = 4

#: Animals allowed to be in the shed awaiting placement at once. Each needs a
#: unit to walk to the shed, pick it up and carry it to a structure, so a queue
#: is fine; but the shed is small and livestock displaces sellable produce.
MAX_IN_FLIGHT = 1


def _structure_for(animal: str) -> str:
    return ANIMALS[animal]["structure"]


class RanchFarm(MelonFarm):
    name = "ranch_farm"

    #: Crop work sits ABOVE hauling. The first ordering put PICKUP and BUILD
    #: between HARVEST and PLANT, and cost ~6,600 whether the herd was 3 animals
    #: or 11 — a flat penalty, which is the signature of a structural bug rather
    #: than a bad herd size. Hauling was starving the crop cycle that funds
    #: everything.
    PRIORITY = (FEED, PICKUP, WATER, PLACE, ANIMAL_HARVEST, CARE,
                HARVEST, PLANT, COLLECT, BUILD, DIG)

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
    def _structures(obs: Obs) -> dict[str, int]:
        """Structures of each kind, occupied or not."""
        out: dict[str, int] = {}
        for t in obs.owned_tiles():
            raw = t.raw
            if isinstance(raw, dict) and raw.get("kind") in ("PASTURE", "COOP"):
                out[raw["kind"]] = out.get(raw["kind"], 0) + 1
        return out

    @staticmethod
    def _vacant_structures(obs: Obs) -> int:
        return sum(1 for t in obs.owned_tiles()
                   if isinstance(t.raw, dict)
                   and t.raw.get("kind") in ("PASTURE", "COOP")
                   and "animal" not in t.raw)

    def _wanted(self, obs: Obs) -> dict[str, int]:
        """Animals still to acquire, per type. Empty once the season is too short."""
        if obs.days_left < HERD_LEAD_DAYS:
            return {}
        have = self._animals(obs)
        shed = obs.shed
        return {a: n - have.get(a, 0) - int(shed.get(a, 0))
                for a, n in HERD.items()
                if n - have.get(a, 0) - int(shed.get(a, 0)) > 0}

    # --- step 1: jobs ---------------------------------------------------------

    def _classify(self, obs: Obs) -> dict[str, list]:
        # The base pass handles plants, weeds and empty tiles; a PASTURE dict is
        # neither `is_plant`, `is_weed` nor `empty`, so it falls through untouched.
        jobs = super()._classify(obs)

        wanted = self._wanted(obs)
        need_struct = {_structure_for(a) for a in wanted}
        have_struct = self._structures(obs)
        carrying_animal = any(
            any(k in ANIMALS and v > 0 for k, v in (inv or {}).items())
            for inv in obs.inventories
        )

        for t in obs.owned_tiles():
            raw = t.raw
            if not isinstance(raw, dict):
                continue
            kind = raw.get("kind")
            if "animal" in raw:
                spec = ANIMALS[raw["animal"]]
                if not raw.get("fed_today", False):
                    jobs[FEED].append(t.pos)
                    continue
                # Harvest only when the held stock is about to hit `max_held`;
                # every unit produced past that cap is discarded.
                if raw.get("yield_units", 0) >= spec["max_held"] - 1 or obs.is_last_day:
                    jobs[ANIMAL_HARVEST].append(t.pos)
                elif raw.get("fertilizer_available", False):
                    jobs[COLLECT].append(t.pos)
                elif not raw.get("cared_today", False):
                    jobs[CARE].append(t.pos)
            elif kind in ("PASTURE", "COOP") and carrying_animal:
                jobs[PLACE].append(t.pos)

        # Build JUST IN TIME — only for livestock already bought and waiting for a
        # home. Building ahead of the herd converts tiles that would grow feed
        # into empty pasture: the first version built 17 structures by day 3 while
        # holding zero animals, and the farm starved.
        homeless = sum(int(obs.shed.get(a, 0)) for a in HERD) + sum(
            int((inv or {}).get(a, 0)) for inv in obs.inventories for a in HERD)
        vacant = self._vacant_structures(obs)
        room = max(0, homeless - vacant)
        if room and need_struct:
            take = min(room, len(jobs[PLANT]))
            jobs[BUILD] = jobs[PLANT][:take]
            jobs[PLANT] = jobs[PLANT][take:]

        # HAUL AT DAWN. Hired hands spawn ON the shed access tiles at hour 0, so
        # for the first turns of the day a pickup costs one turn and no travel;
        # at any other hour it costs a round trip across the board.
        #
        # This is what makes a herd affordable at all. With PICKUP ranked below
        # the crop jobs it never won a unit and the flock never formed (zero
        # sheep, 15,009 coins idle by day 12). Ranked above them it starved the
        # crop cycle instead, costing ~6,600 whatever the herd size. Restricting
        # it to the dawn window lets it rank high without competing: it can only
        # fire in the few turns when it is nearly free.
        if obs.hour <= HAUL_HOURS and self._pickup_item(obs) is not None:
            jobs[PICKUP] = list(obs.shed_tiles)[:2]
        return jobs

    def _pickup_item(self, obs: Obs) -> str | None:
        """What a unit at the shed should collect: animals first, then feed."""
        shed = obs.shed
        for animal in HERD:
            if int(shed.get(animal, 0)) > 0:
                return animal
        n_animals = sum(self._animals(obs).values())
        if not n_animals or int(shed.get(CROP, 0)) <= 0:
            return None
        # Only haul feed when it is actually short: enough carried wheat to feed
        # every animal means the next trip is wasted travel.
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
        inv = invs[unit_idx] if unit_idx < len(invs) else {}
        inv = inv or {}
        if kind == FEED:
            return int(inv.get(CROP, 0)) > 0
        return any(k in ANIMALS and v > 0 for k, v in inv.items())

    def _action_for(self, kind: str, obs: Obs, unit_idx: int, target) -> list:
        if kind == ANIMAL_HARVEST:
            return ["HARVEST"]
        if kind == BUILD:
            # Which structure this tile becomes is decided here, not at classify
            # time, so the choice tracks whatever the herd is still short of.
            wanted = self._wanted(obs)
            have = self._structures(obs)
            for a in sorted(wanted, key=lambda a: -wanted[a]):
                st = _structure_for(a)
                if have.get(st, 0) < HERD[a]:
                    return ["BUILD_PASTURE"] if st == "PASTURE" else ["BUILD_COOP"]
            return ["PASS"]
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

    def _market(self, obs: Obs, plan: TurnPlan, jobs: dict) -> None:
        super()._market(obs, plan, jobs)

        # Animals last: hiring and seed keep the farm alive, and an animal bought
        # into a full shed is silently dropped by the engine.
        if obs.shed_used() >= SHED_CAPACITY - 1:
            return
        if sum(int(obs.shed.get(a, 0)) for a in HERD) >= MAX_IN_FLIGHT:
            return
        # Never buy livestock we cannot feed: two unfed days and it escapes.
        on_hand = int(obs.shed.get(CROP, 0)) + sum(
            int((inv or {}).get(CROP, 0)) for inv in obs.inventories)
        if on_hand < FEED_ON_HAND_BEFORE_BUYING:
            return
        money = obs.money - CASH_RESERVE_FOR_HERD
        for animal, short in sorted(self._wanted(obs).items(),
                                    key=lambda kv: -ANIMALS[kv[0]]["cost"]):
            cost = ANIMALS[animal]["cost"]
            if short > 0 and money >= cost:
                # ONE per turn. Issuing several in a turn empties the purse before
                # the crop economy that funds it has been paid — measured at
                # +17066 for one against +11204 for up to three.
                plan.order("BUY_ANIMAL", animal, 1, priority=PROCUREMENT)
                return

    def _sell_quantity(self, obs: Obs, item: str, qty: int) -> int:
        """Hold back the feed the herd needs; never sell livestock.

        Wheat's season drain is 559 and a full herd eats ~550, so wheat is worth
        more in a sheep than in the market we already glut. Composes with the
        premium throttle inherited from `MelonFarm` — each rule only touches its
        own item.
        """
        if item in ANIMALS:
            return 0  # livestock waiting to be placed, not stock to sell
        qty = super()._sell_quantity(obs, item, qty)
        if item == CROP and not obs.is_last_day:
            qty -= sum(self._animals(obs).values()) * FEED_DAYS_BUFFER
        return qty
