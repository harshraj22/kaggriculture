"""MelonFarm — a WheatFarm that runs a small, deliberately capped melon plot.

This docstring is the specification. `MelonFarm` inherits the entire per-turn
engine of `WheatFarm` (tile classification, priority order, unit assignment,
hiring, land, execution) and changes exactly three things: which crop goes into
an empty tile, which seeds it buys, and how it sells. Read `wheat_farm.py` for
the base algorithm; everything melon-specific is below.

## The idea

Melon is the highest-value crop in the game and the easiest to lose money on.
The tile arithmetic is seductive — 6 units at a 250 base against wheat's 4 at 25,
for a similar number of actions — yet an all-melon farm scores 18. Two separate
limits bite, and both are about capacity rather than rate.

**Cash flow.** Melon costs 80 a seed and pays nothing until `first_yield_day` 10.
Sowing 25 tiles is 2,000 of a 3,000 purse, leaving nothing for the payroll that
keeps them watered — and an unwatered plant is a weed by day 2. So wheat, which
returns from day 2, funds the farm and melon is bought out of realised profit.

**Market depth, which binds long before production does.** Wheat gluts on a `log`
curve; melon gluts on `sq` with `above_target 3.60`:

    price(x) = 250 - 0.01 * x**2        (x = units sold above I0 = 10000)

    x =  50 -> 225      x = 100 -> 150
    x = 140 ->  54      x = 158 -> $1 floor

No shop in `SHOPS` demands melon either — only the town centre, at 1 per 12 turns
— so the glut never clears. Revenue is maximised near **150 units for the season**
(~26,000); every melon after that is worth pennies.

That number sizes the plot, and nothing else does:

    150 sellable units / 6 units per harvest / ~2.5 harvests per tile ~= 10 tiles

## What it overrides

    _premium_wanted  How many melon tiles are still wanted: `premium_tiles` minus
                     those alive, and zero once fewer than `PREMIUM_LEAD_DAYS`
                     remain, since a melon sown too late never reaches
                     first_yield_day and wastes both the seed and the tile.
    _seed_targets    Buy melon seed for the shortfall (capped by empty tiles) and
                     wheat seed for every remaining empty tile.
    _crop_for        Sow melon while the plot is under cap AND unqueued melon seed
                     is in hand, else wheat. The caller increments `alive` as
                     plants are queued, so the cap holds within a single turn as
                     well as across turns — otherwise every idle unit sows melon
                     on the same tick.
    _sell            Dump wheat unthrottled; meter melon against `dump_capacity`
                     at `PREMIUM_FLOOR_RATIO`, which re-reads market inventory
                     each turn and so sells less as the glut deepens. On the last
                     day, dump everything: unsold stock scores zero.

## Sizing was validated head-to-head, not against `starter`

Tuning against the built-in `starter` picks 14 tiles (40794). That is overfitting:
`starter` barely competes for the same goods, and the ladder is other competitors.
In a round-robin where both seats run real strategies, 14 **loses** to 10, and 10
is the only size with a positive worst case:

    melon10     mean margin  +9991    worst   +1458
    melon14                  +9255            -1458
    melon6                   +5342            -2198
    melon18                  -5563           -14465
    wheat_farm              -19025           -25093

Scores 37354 (train) / 37766 (holdout) on protocol v1. Note the edge is
market-coupled: ~37k against a passive opponent but ~27k in a mirror match, where
two farms flood the same melon market.
"""

from ..game.actions import TurnPlan
from ..game.market import dump_capacity
from ..game.observation import Obs
from .wheat_farm import CROP, PLANT, WheatFarm

PREMIUM = "MELON"

#: Melon tiles to hold. Set by market depth, not by tile yield — see the module
#: docstring. Validated head-to-head against other real strategies, not `starter`.
PREMIUM_TILES = 10

#: Stop sowing melon with fewer than this many days left. Melon's `max_yield_day`
#: is 12, so one sown later cannot reach peak yield; it occupies a tile that wheat
#: would have cycled twice, and the 80-coin seed is a write-off.
PREMIUM_LEAD_DAYS = 12

#: Sell melon down to this fraction of the current price, no further. Re-read from
#: live market inventory each turn, so the throttle tightens by itself as the glut
#: deepens rather than needing a schedule.
PREMIUM_FLOOR_RATIO = 0.85


class MelonFarm(WheatFarm):
    name = "melon_farm"

    PREMIUM = PREMIUM

    # --- how many melon tiles we want ------------------------------------------

    def _premium_wanted(self, obs: Obs, alive: int) -> int:
        if obs.days_left < PREMIUM_LEAD_DAYS:
            return 0
        return max(0, PREMIUM_TILES - alive)

    # --- the three overrides ---------------------------------------------------

    def _seed_targets(self, obs: Obs, jobs: dict) -> dict[str, int]:
        """Melon first, wheat with what is left.

        Order matters: `_market` walks this dict in order and spends the budget as
        it goes, so melon — the scarce, expensive, capacity-limited crop — gets
        first claim, and wheat soaks up the remainder.
        """
        empty = len(jobs[PLANT])
        melon = min(self._premium_wanted(obs, self._premium_alive(obs)), empty)
        return {PREMIUM: melon, CROP: empty - melon}

    def _crop_for(self, obs: Obs, alive: int) -> str:
        """Melon while under cap and seed is in hand; wheat otherwise.

        `alive` already includes anything queued earlier this turn — the base
        engine increments it as it commits plants. Without that, all nine units
        would read the same pre-turn count and sow melon simultaneously.
        """
        if self._premium_wanted(obs, alive) <= 0:
            return CROP
        # Seeds queued this turn are not deducted from `obs.seeds` until the market
        # resolves, so compare against how many melons are already committed.
        queued = max(0, alive - self._premium_alive(obs))
        return PREMIUM if int(obs.seeds.get(PREMIUM, 0)) > queued else CROP

    def _sell(self, obs: Obs, plan: TurnPlan) -> None:
        """Wheat unthrottled, melon metered — except on the last day.

        Wheat's `log` glut curve is shallow enough that dumping barely moves it.
        Melon's is `sq`: 158 units takes it from 250 to the 1-coin floor, and no
        shop buys melon to clear the backlog. Metering against `dump_capacity`
        keeps the marginal sale above half the going price.

        The last day is the exception. Unsold stock scores zero, so a throttle
        that protects tomorrow's price is pure loss when there is no tomorrow.
        """
        last_day = obs.is_last_day
        for item, qty in obs.shed.items():
            qty = int(qty)
            if qty <= 0:
                continue
            if item == PREMIUM and not last_day:
                qty = min(qty, dump_capacity(
                    item, obs.market_inventory.get(item, 0), PREMIUM_FLOOR_RATIO))
            if qty > 0:
                plan.sell(item, qty)
