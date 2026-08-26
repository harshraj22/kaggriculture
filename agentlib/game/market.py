"""Sale planning on top of the env's price curve.

The curve itself is `kaggle_environments`' `market_price` — imported, never
reimplemented. What lives here is the one question the env doesn't answer
directly: selling moves the price, so how much can we dump before we've walked
it down ourselves?

Deliberately small. This module previously carried `sell_revenue`, `buy_cost`
and `marginal_sell_price`, written in anticipation of sale-timing logic that no
strategy ever needed — three functions with zero callers. They're a few lines
each to bring back against `market_price` when a strategy actually wants them,
and carrying unused code that shapes how we think is worse than rewriting it.
"""

from .config import PRICE_FLOOR, market_price


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
