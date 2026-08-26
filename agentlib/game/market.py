"""Sale planning on top of the env's price curve.

The curve itself is `kaggle_environments`' `market_price` — imported, not
reimplemented. What lives here is the question the env doesn't answer directly:
selling moves the price, so what does dumping N units actually net?
"""

from .config import PRICE_FLOOR, market_price


def sell_revenue(resource, qty, inventory, opponent_qty=0, params=None):
    """Total coins from selling `qty` units, accounting for the price sliding down.

    Sells are quoted pre-sell, one unit at a time. `opponent_qty` models the
    opponent dumping the same product on the same turn — their units also depress
    the price we receive for our later units, since orders interleave.
    """
    inv = inventory
    total = 0
    for i in range(qty):
        px = market_price(resource, inv, params)
        total += px
        # A unit sold at the floor is not added to market inventory.
        if px > PRICE_FLOOR:
            inv += 1
        if i < opponent_qty:
            inv += 1
    return total


def marginal_sell_price(resource, qty, inventory, params=None):
    """Average per-unit price for selling `qty` units — 'how much is too much'."""
    return sell_revenue(resource, qty, inventory, params=params) / qty if qty else 0.0


def buy_cost(resource, qty, inventory, params=None):
    """Total coins to buy `qty` units. Buys are quoted post-buy."""
    inv = inventory
    total = 0
    for _ in range(qty):
        inv -= 1
        total += market_price(resource, inv, params)
    return total


def dump_capacity(resource, inventory, floor_ratio=0.5, params=None):
    """How many units can be sold before the marginal price drops below
    `floor_ratio` x the current price. A crude 'don't crash your own market' guard."""
    limit = market_price(resource, inventory, params) * floor_ratio
    inv = inventory
    n = 0
    while n < 10_000:
        px = market_price(resource, inv, params)
        if px < limit:
            break
        n += 1
        if px > PRICE_FLOOR:
            inv += 1
    return n
