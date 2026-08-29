"""WheatFarm — keep every owned tile planted, watered, and harvested at peak yield.

This docstring is the specification: the engine rules the design rests on, the
per-turn algorithm, the tuned constants, and the variants that were measured and
rejected. It is meant to be sufficient to reimplement from, without reading the
code below or the engine source.

## What it fixes

Sampled every 12 turns over a full episode, `safe_farmer` holds a mean of **1.0**
live plants and `wheat_loop` **1.9**, out of 25 owned tiles; both score ~3,900.
The bottleneck is land utilisation and action routing, not crop choice.

## Engine rules the design depends on

All verified against kaggle-environments 1.32.7.

1. **Yield window.** A one-time crop is sown with `yield_units = 1`. `WATER` adds
   +1 only while `(max_yield_day + 1) // 2 <= age_days <= max_yield_day` — ages
   2–4 for wheat — and `max_yield` caps the total.
2. **`HARVEST` is a silent no-op before `first_yield_day`** (age 2 for wheat) and
   still costs the unit's turn. It moves `yield_units` into the *unit's*
   inventory, and clears a one-time crop's tile to empty.
3. **Death by thirst.** A plant is created with `consecutive_unwatered = 1`; the
   daily refresh increments it on an unwatered day and converts the tile to WEED
   at 2. A plant not watered the day it is sown is dead by morning.
4. **`PLANT` is atomic per crop.** If a turn requests more plants of one crop than
   seeds held, the engine drops *every* `PLANT` of that crop for that turn.
5. **Market resolves after unit actions**, so seeds bought this turn cannot be
   planted until the next one.
6. **`SELL` spends from the shed only**, while `HARVEST` fills a unit's inventory.
   The end-of-day drop moves inventories into the shed, caps it at 100, and
   **discards** the overflow.
7. **Hiring** costs `fib(n)` for the n-th hire of the day, resets daily, and hands
   vanish each evening. Cumulative: 8 hands = 54 coins, 12 hands = 376.
8. **Land** unlocks in the fixed order NE, SW, SE at 1000 / 2000 / 4000.

Rules 1 and 2 give the wheat cycle:

    plant day D (yield 1) -> water D+2, D+3, D+4 (yield 4) -> harvest day D+4

which is 4 units per 5 tile-days (0.80/tile/day), against 0.67 for harvesting at
the first legal moment.

## Algorithm, per turn

**Step 1 — classify every owned tile** into exactly one job:

    plant, not watered today ................................. WATER
    plant, watered, yield_units > 0, age >= first_yield_day,
        and (ongoing or age >= max_yield_day) ................ HARVEST
    weed ..................................................... DIG
    empty .................................................... PLANT

Priority is **WATER > HARVEST > PLANT > DIG**. Watering is survival *and* yield;
harvesting banks the crop and frees the tile; planting starts the next cycle;
digging only recovers land already lost.

**Step 2 — issue market orders**, each conditional:

    Land   buy the next quadrant when all three hold: we are under
           `max_quadrants`; the rest of the season can repay it
           `LAND_PAYBACK_MARGIN` times at `LAND_DAILY_YIELD`/day; and money covers
           price + `WAGE_DAYS_BUFFER` days of payroll at the NEW size + seed for
           every tile at the NEW size.
    Hire   up to clamp(owned * hands_per_tile, min_hands, max_hands), taking as
           many as affordable rather than all-or-nothing.
    Seed   per crop, top up to the number of empty tiles, spending at most
           money - CASH_RESERVE.
    Sell   the entire shed, every turn (see rule 6).

**Step 3 — assign units to jobs.** For each priority group in order, repeatedly
commit the globally closest (idle unit, unclaimed tile) pair by Manhattan
distance, until units or jobs run out. Jobs beyond the unit count wait.

**Step 4 — execute.** A unit standing on its target performs the job; otherwise it
takes one greedy step toward it. `PLANT` is emitted only while the count already
emitted this turn for that crop is below the seeds held (rule 4).

## Why assignment is task-centric

Giving each *job* its nearest unit, rather than letting each *unit* pick its
nearest job, is worth **5241 -> 11512** on its own. Scanning jobs in board order
strands work: a tile early in the scan claims a distant unit and leaves the work
next door to whoever happens to be free last. Travel, not the wage bill, is what
caps how many tiles a farm can keep alive.

**Step 1b — cap sowing at the labour available.** Watering is a *daily*
obligation and a plant missed twice is a weed, so sowing past capacity is not
slower growth, it is a die-off. Live plants are capped at
`MAX_PLANTS_PER_UNIT` per unit.

## Measured and rejected

All on protocol v1/train, 60 paired episodes, kaggle-environments 1.32.7.
Scores are the final bank; starting money is 3000.

- **No sowing cap** — the farm sowed every empty tile it could afford, peaked near
  50 live plants and collapsed to 9 live with 18 weeds by day 5. Capping at 4.0
  plants/unit is worth **14170 -> 15873**. Neighbouring values are clearly worse
  (3.0: 14669, 4.5: 14125, 5.0: 14563, 7.0: 14331), so this is a real optimum and
  not noise — sd is ~600 and the gaps are 2-3x that.
- **Sowing in the last turn of the day** — a seed seed sown at hour 23 cannot be
  watered before the refresh, so rule 3 turns it to weed by morning. It costs 10
  coins, a unit-turn, and leaves a tile only a DIG can recover. Worth
  **9882 -> 11442** on its own.
- **A third quadrant** — 75 tiles, **11442 vs 14170** for two. Weeds ran 20-38 of
  75 and never recovered: measured unit-turns are ~60% movement, and past 50 tiles
  the travel bill eats the watering capacity that keeps plants alive. `DIG` never
  once fired at 75 tiles, because it is the lowest priority and some tile is
  always thirsty.
- **More hands** — 8 is the peak. 6: 13362. 10: **11304** — and note this only
  changes anything once `hands_per_tile` is raised too, since 50 tiles x 0.16
  already sits exactly on the cap of 8.

Scores **15873** (train) / **15904** (holdout) on protocol v1, and beats the
built-in `starter` by 12336 in 60 of 60 games on v3.
"""

from ..game.actions import INVESTMENT, PROCUREMENT, TurnPlan, manhattan, move_toward
from ..game.config import (
    CROPS,
    LAND_PRICES,
    QUADRANT_SIZE,
    TURNS_PER_DAY,
    hire_cost,
)
from ..game.observation import Obs
from .base import Strategy

CROP = "WHEAT"

#: Jobs in priority order. Step 3 fills each group completely before the next.
WATER, HARVEST, PLANT, DIG = "WATER", "HARVEST", "PLANT", "DIG"
PRIORITY = (WATER, HARVEST, PLANT, DIG)

#: Total quadrants to hold, NW included. Measured: 2 extra is the peak; a third
#: costs more in unwaterable tiles than it returns.
MAX_QUADRANTS = 2

#: Labour. `max_hands` is a measured cliff, not a budget guess — 8 scores 15037,
#: 12 scores 3486, because hands past that spend the day walking.
HANDS_PER_TILE = 0.16
MIN_HANDS = 2
MAX_HANDS = 8

#: Never spend the last of the cash on seed; a farm with no float cannot make
#: payroll, and unpaid tiles go to weed within two days.
CASH_RESERVE = 300.0

#: Live plants one unit can keep alive, including travel.
#:
#: Watering is a DAILY obligation and a plant missed twice is a weed, so sowing
#: past the labour available is not slower growth — it is a die-off. Measured
#: unit-turns run ~60% movement, so a unit completes roughly 8 jobs a day and must
#: cover watering, harvesting and replanting out of that. Without this cap the
#: farm sowed every empty tile it could afford, peaked around 50 live plants, and
#: collapsed to 9 with 18 weeds by day 5.
MAX_PLANTS_PER_UNIT = 4.0

#: Land guards. Coins per owned tile per day, used only to ask whether the
#: remaining season can repay a quadrant; deliberately below the ~20 that peak
#: wheat throughput implies, because new land does not reach peak immediately.
LAND_DAILY_YIELD = 8.0
LAND_PAYBACK_MARGIN = 2.0
WAGE_DAYS_BUFFER = 3


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _payroll(n_hands: int) -> float:
    """Cumulative cost of hiring `n_hands` in one day (rule 7)."""
    return sum(hire_cost(i) for i in range(max(0, n_hands)))


class WheatFarm(Strategy):
    name = "wheat_farm"

    #: Stateless by construction: every turn re-derives jobs and re-runs the
    #: assignment from the observation alone. `wheat_loop` cached routes across
    #: turns and spent its complexity budget invalidating them.

    #: A second, capped crop grown alongside wheat. `None` here means wheat only;
    #: `MelonFarm` sets it. The three hooks below are the entire seam — everything
    #: else (classification, priority, assignment, hiring, land, execution) is
    #: shared, so a variant is a subclass with three short overrides rather than a
    #: fork of the engine.
    PREMIUM: str | None = None

    #: Job kinds in priority order. A subclass that adds jobs (animals, hauling)
    #: overrides this; `_classify` and `_assign` both read it, so the two cannot
    #: drift apart.
    PRIORITY = PRIORITY

    def _can_do(self, kind: str, obs: Obs, unit_idx: int) -> bool:
        """Whether this unit may take this job kind.

        Exists because some jobs need something IN THE UNIT'S HANDS: `FEED` spends
        wheat from the acting unit's inventory, `PLACE` spends the animal from it.
        Assignment must filter on that, or the nearest unit wins a job it cannot
        perform and the turn is wasted.
        """
        return True

    def _action_for(self, kind: str, obs: Obs, unit_idx: int, target) -> list:
        """The action a unit standing on `target` performs for this job kind."""
        return [kind]

    def _premium_alive(self, obs: Obs) -> int:
        """Owned tiles currently growing the premium crop."""
        if not self.PREMIUM:
            return 0
        return sum(1 for t in obs.owned_tiles()
                   if t.is_plant and t.get("crop") == self.PREMIUM)

    def _seed_targets(self, obs: Obs, jobs: dict) -> dict[str, int]:
        """Desired seed HOLDINGS per crop; `_market` buys the shortfall in order."""
        return {CROP: len(jobs[PLANT])}

    def _crop_for(self, obs: Obs, alive: int) -> str:
        """Which crop goes into the next empty tile. `alive` counts the premium
        crop already growing PLUS any queued earlier this same turn."""
        return CROP

    def _sell(self, obs: Obs, plan: TurnPlan) -> None:
        """Sell everything, every turn: the shed caps at 100 and the end-of-day
        drop discards the overflow, so an unsold shed is thrown-away harvest.

        The loop is fixed; subclasses adjust `_sell_quantity` per item. That split
        exists so two independent sale policies can COMPOSE — a premium-crop
        throttle and an animal-feed reserve are both "sell less of one item", and
        before this they were rival `_sell` overrides that could not coexist.
        """
        for item, qty in obs.shed.items():
            n = self._sell_quantity(obs, item, int(qty))
            if n > 0:
                plan.sell(item, n)

    def _sell_quantity(self, obs: Obs, item: str, qty: int) -> int:
        """How many of `item` to sell this turn. Default: all of it."""
        return qty

    def act(self, obs: Obs) -> dict:
        plan = TurnPlan(n_hands=len(obs.hands))
        jobs = self._classify(obs)
        self._market(obs, plan, jobs)
        self._assign(obs, plan, jobs)
        return plan.to_dict()

    # --- step 1 ---------------------------------------------------------------

    def _classify(self, obs: Obs) -> dict[str, list]:
        """Every owned tile into exactly one job. Positions only; no tile objects."""
        jobs: dict[str, list] = {k: [] for k in self.PRIORITY}
        for tile in obs.owned_tiles():
            if tile.is_plant:
                if not tile.get("watered_today", False):
                    jobs[WATER].append(tile.pos)
                elif self._ripe(obs, tile):
                    jobs[HARVEST].append(tile.pos)
            elif tile.is_weed:
                jobs[DIG].append(tile.pos)
            elif tile.empty:
                jobs[PLANT].append(tile.pos)

        live = len(jobs[WATER]) + len(jobs[HARVEST])
        room = int((1 + len(obs.hands)) * MAX_PLANTS_PER_UNIT) - live
        if room <= 0 or not self._can_still_water_today(obs):
            jobs[PLANT] = []
        else:
            jobs[PLANT] = jobs[PLANT][:room]
        return jobs

    @staticmethod
    def _can_still_water_today(obs: Obs) -> bool:
        """Never sow a seed that cannot be watered before the day ends.

        Rule 3: a plant is created with `consecutive_unwatered = 1`, and the daily
        refresh turns it to WEED at 2. A seed sown on the last turn of the day is
        therefore dead by morning — it costs 10 coins, a unit-turn, and leaves a
        weed that only a DIG can clear.

        This is what made the third quadrant look unworkable: weeds climbed from 2
        to 38 of 75 tiles and never came back, because DIG is the lowest priority
        and never gets a spare unit while any tile is thirsty.
        """
        return obs.hour <= TURNS_PER_DAY - 2

    @staticmethod
    def _ripe(obs: Obs, tile) -> bool:
        """Hold a one-time crop until `max_yield_day`.

        Harvesting the moment it is legal takes 2 units off a 3-tile-day cycle
        (0.67/tile/day); waiting for the last watering takes 4 off 5 (0.80). The
        `watered_today` check upstream matters here — that final watering is what
        adds the fourth unit, so the tile only becomes ripe after it lands.
        """
        if tile.get("yield_units", 0) <= 0:
            return False
        crop = CROPS.get(tile.get("crop"))
        if crop is None:
            return True
        age = obs.day - int(tile.get("planted_day", obs.day))
        if age < crop["first_yield_day"]:
            return False  # HARVEST would be a silent no-op and waste the turn
        if crop["ongoing"]:
            return True
        # Saturation, not the calendar. Watering adds +1 per day inside the
        # window but `max_yield` caps the total, so a crop can hit its ceiling
        # BEFORE `max_yield_day` and every further day is a dead tile-day.
        # Melon saturates at 6 units on age 10 against a max_yield_day of 12:
        # holding to the calendar wasted two tile-days on every melon grown.
        # Wheat never saturates (reaches 4 of a possible 6), so it is unaffected.
        return (tile.get("yield_units", 0) >= crop["max_yield"]
                or age >= crop["max_yield_day"])

    # --- step 2 ---------------------------------------------------------------

    def _market(self, obs: Obs, plan: TurnPlan, jobs: dict) -> None:
        owned = sum(1 for _ in obs.owned_tiles())
        money = obs.money

        if self._should_buy_land(obs, owned, money):
            plan.buy_land()
            money -= LAND_PRICES[len(obs.unlocked_quadrants) - 1]

        money -= self._hire(obs, plan, owned, money)

        # Seeds are for the empty tiles we can actually plant next turn (rule 5).
        # Iterated in the order `_seed_targets` returns them, so a subclass can put
        # the crop it cares about first and let it win the budget.
        budget = max(0.0, money - CASH_RESERVE)
        for crop, target in self._seed_targets(obs, jobs).items():
            want = int(target) - int(obs.seeds.get(crop, 0))
            if want <= 0:
                continue
            cost = CROPS[crop]["seed"]
            n = min(want, int(budget // cost))
            if n > 0:
                plan.order("BUY_SEED", crop, n, priority=PROCUREMENT)
                budget -= n * cost

        self._sell(obs, plan)

    def _should_buy_land(self, obs: Obs, owned: int, money: float) -> bool:
        n_unlocked = len(obs.unlocked_quadrants)
        if n_unlocked >= MAX_QUADRANTS or n_unlocked > len(LAND_PRICES):
            return False
        price = LAND_PRICES[n_unlocked - 1]

        # Can the rest of the season repay it? Land bought late is land that never
        # pays for itself, whatever the bank says.
        new_tiles = QUADRANT_SIZE * QUADRANT_SIZE
        if obs.days_left * new_tiles * LAND_DAILY_YIELD < price * LAND_PAYBACK_MARGIN:
            return False

        # Can we still work it? Buying land is only an investment if the payroll
        # and the seed for the bigger farm survive the purchase — otherwise the
        # new tiles go unwatered and 50 plants become 3 in two days.
        after = owned + new_tiles
        hands = _clamp(int(after * HANDS_PER_TILE), MIN_HANDS, MAX_HANDS)
        commitments = price + WAGE_DAYS_BUFFER * _payroll(hands) + after * CROPS[CROP]["seed"]
        return money >= commitments

    def _hire(self, obs: Obs, plan: TurnPlan, owned: int, money: float) -> float:
        """Top up to the target, taking as many as affordable. Returns the spend."""
        target = _clamp(int(owned * HANDS_PER_TILE), MIN_HANDS, MAX_HANDS)
        missing = target - len(obs.hands)
        if missing <= 0:
            return 0.0

        # Partial, not all-or-nothing: `fib` pricing means the cheap hands are
        # very cheap, and refusing the whole order because the last one is
        # unaffordable leaves the farm unstaffed for the day.
        budget = max(0.0, money - CASH_RESERVE)
        spent, taken = 0.0, 0
        already = obs.hires_today
        while taken < missing:
            cost = hire_cost(already + taken)
            if spent + cost > budget:
                break
            spent += cost
            taken += 1
        if taken:
            plan.order("HIRE", taken, priority=INVESTMENT)
        return spent

    # --- steps 3 and 4 --------------------------------------------------------

    def _assign(self, obs: Obs, plan: TurnPlan, jobs: dict) -> None:
        units = [obs.farmer, *obs.hands]
        idle = set(range(len(units)))
        crops = [CROP] + ([self.PREMIUM] if self.PREMIUM else [])
        seeds_left = {c: int(obs.seeds.get(c, 0)) for c in crops}
        alive = self._premium_alive(obs)

        for kind in self.PRIORITY:
            targets = list(jobs.get(kind) or [])
            if kind == PLANT:
                # Rule 4 is atomic PER CROP: requesting more PLANTs of one crop
                # than seeds of that crop drops every one of them. Accounted per
                # crop below; here we only cap the group by total seed on hand.
                targets = targets[:sum(seeds_left.values())]
            while idle and targets:
                # Task-centric: the closest (job, unit) pair globally, not the
                # closest job to whichever unit we happen to look at first.
                pairs = [(u, t) for u in idle for t in targets
                         if self._can_do(kind, obs, u)]
                if not pairs:
                    break
                unit_idx, target = min(
                    pairs,
                    key=lambda pair: (manhattan(units[pair[0]], pair[1]), pair[0], pair[1]),
                )
                idle.discard(unit_idx)
                targets.remove(target)

                pos = units[unit_idx]
                if tuple(pos) != tuple(target):
                    plan.set_unit(unit_idx, move_toward(pos, target) or ["PASS"])
                    continue

                if kind != PLANT:
                    plan.set_unit(unit_idx, self._action_for(kind, obs, unit_idx, target))
                    continue

                crop = self._crop_for(obs, alive)
                if seeds_left.get(crop, 0) <= 0:
                    crop = CROP if seeds_left.get(CROP, 0) > 0 else None
                if crop is None:
                    plan.set_unit(unit_idx, ["PASS"])
                    continue
                seeds_left[crop] -= 1
                if crop == self.PREMIUM:
                    # Incremented as plants are QUEUED, not as they appear on the
                    # board next turn: without this every idle unit sees the same
                    # `alive` and sows the premium crop on the same tick, blowing
                    # through the cap in one turn.
                    alive += 1
                plan.set_unit(unit_idx, [PLANT, crop])

        for unit_idx in idle:
            plan.set_unit(unit_idx, ["PASS"])
