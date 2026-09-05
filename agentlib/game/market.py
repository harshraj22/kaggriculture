"""Sale planning on top of the env's price curve.

The curve itself is `kaggle_environments`' `market_price` — imported, never
reimplemented. What lives here are the two questions the env doesn't answer
directly: selling moves the price, so how much can we dump before we've walked
it down ourselves — and how fast does the town drink each good back down again?

Deliberately small. This module previously carried `sell_revenue`, `buy_cost`
and `marginal_sell_price`, written in anticipation of sale-timing logic that no
strategy ever needed — three functions with zero callers. They're a few lines
each to bring back against `market_price` when a strategy actually wants them,
and carrying unused code that shapes how we think is worse than rewriting it.
`drain_per_day` and `depth_to_price` below earn their place: `market_farm` sizes
its entire crop portfolio out of them.
"""

from .config import (
    MARKET_PARAMS,
    MAX_SHOP_INSTANCES,
    PRICE_FLOOR,
    SHOPS,
    SINGLE_PRODUCT_SHOPS,
    TOWN_CENTER_PRODUCTS,
    TOWN_CENTER_SELL_INTERVAL,
    TOWN_SHOP_SELL_INTERVAL,
    TOWN_SHOP_UNLOCK_INTERVAL,
    TURNS_PER_DAY,
    market_price,
)


def dump_capacity(resource: str, inventory: float, floor_ratio: float = 0.5, params=None) -> int:
    """How many units can be sold before the marginal price falls below
    `floor_ratio` × the current price.

    A "don't crash your own market" guard. Matters most for the premium goods —
    strawberry, melon, milk and wool all have `above_target > 1`, so roughly one
    field's output takes them to the $1 floor.
    """
    limit = market_price(resource, inventory, params) * floor_ratio
    inv = inventory
    n = 0
    while n < 10_000:
        px = market_price(resource, inv, params)
        if px < limit:
            break
        n += 1
        # A unit sold at the floor is not added to market inventory, so the
        # price stops moving and this would otherwise never terminate.
        if px > PRICE_FLOOR:
            inv += 1
    return n


#: Hard stop for the searches below. The price curve flattens at `PRICE_FLOOR`,
#: so any target at or under it is satisfiable by an unbounded number of units
#: and an unguarded search would not terminate.
_SEARCH_CAP = 100_000


def depth_to_price(item: str, inventory: float, target_price: float, params=None) -> int:
    """Units sellable from `inventory` before the marginal price drops under
    `target_price`.

    The difference from `dump_capacity` is the reference point, and it matters
    more than it looks. `dump_capacity` measures against the price *right now*,
    so once a market has already been flooded it happily keeps selling — half of
    a crashed price is still "within the floor ratio". This measures against an
    absolute target, which is what you want when the target is derived from the
    good's own `base`: it says "stop selling melon at 7 coins" rather than "melon
    at 7 is fine, it was 14 a moment ago".
    """
    if market_price(item, inventory, params) < target_price:
        return 0

    # Price is monotonically non-increasing in inventory, so bracket then bisect
    # rather than walking: the sizing code below asks about hundreds of units.
    lo, hi = 0, 1
    while hi < _SEARCH_CAP and market_price(item, inventory + hi, params) >= target_price:
        lo, hi = hi, hi * 2
    if hi >= _SEARCH_CAP:
        return _SEARCH_CAP
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if market_price(item, inventory + mid, params) >= target_price:
            lo = mid
        else:
            hi = mid
    # `lo` is the largest offset still at or above target; the unit sold AT that
    # offset is the last good one, hence the +1.
    return lo + 1


def drain_per_day(item: str, unlocked_shops=(), turns_per_day: int = TURNS_PER_DAY,
                  shop_interval: int = TOWN_SHOP_SELL_INTERVAL,
                  center_interval: int = TOWN_CENTER_SELL_INTERVAL) -> float:
    """Units of `item` the town removes from the market each day, right now.

    This is the number the whole portfolio hangs off. A good's price only stays
    near `base` while supply matches this, so the drain — not the tile yield, not
    the base price — is what says how many tiles a crop deserves.

    Two consumers, per the env's `_town_consume`:

    * **Shops**, every `shop_interval` steps. Each unlocked INSTANCE consumes one
      of every product it demands, and a shop demanding exactly one product
      consumes two. Instances are drawn with replacement, so the same shop can
      appear several times and each copy drinks independently — which is why this
      takes the live `unlocked_shops` list rather than the `SHOPS` table.
    * **The town centre**, every `center_interval` steps, one of each product in
      `TOWN_CENTER_PRODUCTS`. Note FERTILIZER is not in that list and no shop
      demands it, so its drain is exactly zero: every fertilizer sold is sold
      into a market that never recovers.

    Fractional by design — the shop tick does not divide the day evenly for every
    configuration, and callers want a rate to multiply by `days_left`, not an
    integer count of anything.
    """
    ticks = turns_per_day / max(1, shop_interval)
    per_tick = sum(
        2 if name in SINGLE_PRODUCT_SHOPS else 1
        for name in unlocked_shops
        if item in SHOPS.get(name, ())
    )
    centre = (turns_per_day / max(1, center_interval)
              if item in TOWN_CENTER_PRODUCTS else 0.0)
    return per_tick * ticks + centre


#: Mean drain contribution of ONE not-yet-drawn shop instance, per shop tick.
#: Instances are drawn uniformly with replacement from `SHOPS`, so this is the
#: expectation over that draw — the only honest thing to say about a shop that
#: has not unlocked yet.
_MEAN_PER_INSTANCE = {
    item: sum((2 if name in SINGLE_PRODUCT_SHOPS else 1)
              for name, products in SHOPS.items() if item in products) / len(SHOPS)
    for item in {p for products in SHOPS.values() for p in products}
}


def projected_drain_per_day(item: str, unlocked_shops=(), days_left: int = 0,
                            turns_per_day: int = TURNS_PER_DAY,
                            shop_interval: int = TOWN_SHOP_SELL_INTERVAL,
                            center_interval: int = TOWN_CENTER_SELL_INTERVAL,
                            unlock_interval: int = TOWN_SHOP_UNLOCK_INTERVAL,
                            max_instances: int = MAX_SHOP_INSTANCES) -> float:
    """Average daily drain over the REST of the season, not just today's.

    Sizing a farm off `drain_per_day` alone cripples the opening. On day 0 no
    shop has unlocked, so every good's drain is the town centre's 1/day and the
    plan asks for one tile of everything — at exactly the moment the farm needs
    volume to fund itself. But shops keep arriving (one instance every
    `unlock_interval` days, up to `max_instances`), and a crop sown today is sold
    into the market that exists when it matures, not the one that exists now.

    So this adds the instances still to come, at the mean composition of the
    `SHOPS` table, and halves their contribution because they arrive spread
    across the remaining days rather than all at once. Already-unlocked shops are
    counted at their real composition: three YARN_STOREs is a fact about this
    episode, not an expectation.
    """
    now = drain_per_day(item, unlocked_shops, turns_per_day, shop_interval,
                        center_interval)
    room = max(0, max_instances - len(list(unlocked_shops)))
    coming = min(room, max(0, days_left) / max(1, unlock_interval))
    ticks = turns_per_day / max(1, shop_interval)
    return now + 0.5 * coming * _MEAN_PER_INSTANCE.get(item, 0.0) * ticks


def base_price(item: str, params=None) -> float:
    """The good's undisturbed price — what it fetches at `I0`."""
    return float((params or MARKET_PARAMS)[item]["base"])
