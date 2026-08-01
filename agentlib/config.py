"""Game constants — re-exported from the environment, not transcribed.

`kaggle_environments.envs.kaggriculture.kaggriculture` defines every table at module
level. This file exists only to give the rest of the codebase one import site and to
hold the handful of things the env genuinely doesn't expose as constants.

Anything you're tempted to type a number into: check the env module first.
"""

import json
from os import path

from kaggle_environments.envs.kaggriculture import kaggriculture as _env

# --- straight re-exports ------------------------------------------------------

CROPS = _env.CROPS  # {"WHEAT": {"seed", "first_yield_day", "max_yield_day", "interval", "max_yield", "ongoing"}}
ANIMALS = _env.ANIMALS  # {"GOOSE": {"cost", "structure", "first_yield_day", "interval", "max_held", "product"}}
PRODUCTS = _env.PRODUCTS

MARKET_PARAMS = _env.MARKET_PARAMS  # {"WHEAT": {"base", "I0", "T", "below_func", ...}}
MARKET_I0 = _env.MARKET_I0
PRICE_FLOOR = _env.PRICE_FLOOR
market_price = _env.market_price  # market_price(item, inventory, params=None) -> int

SHOPS = _env.SHOPS
TOWN_CENTER_PRODUCTS = _env.TOWN_CENTER_PRODUCTS
TOWN_CENTER_DEMAND_SCHEDULE = _env.TOWN_CENTER_DEMAND_SCHEDULE

MOVE_DELTA = _env.FARMER_MOVES  # {"NORTH": (0, -1), ...}; y grows downward
MOVES = tuple(MOVE_DELTA)

# Quadrant unlock order is FIXED — you do not choose which one you buy next.
LAND_ORDER = _env.LAND_ORDER  # ["NE", "SW", "SE"]; NW is always unlocked
LAND_PRICES = _env.LAND_PRICES  # [1000, 2000, 4000]

FARM_HAND_COST_MULT = _env.FARM_HAND_COST_MULT
fib = _env._fib  # fib(0)=1, fib(1)=1, fib(2)=2, ...
hire_cost = _env._hire_cost  # hire_cost(n_already_today, mult=1) -> cost of next hire

# --- configuration defaults, read from the env's own spec ---------------------

_SPEC_PATH = path.join(path.dirname(_env.__file__), "kaggriculture.json")
with open(_SPEC_PATH) as _f:
    SPEC = json.load(_f)


def default(key, fallback=None):
    """Default value of a configuration knob, per kaggriculture.json."""
    entry = SPEC.get("configuration", {}).get(key, fallback)
    if isinstance(entry, dict):
        return entry.get("default", fallback)
    return entry


EPISODE_STEPS = default("episodeSteps")
TURNS_PER_DAY = default("turnsPerDay")
BOARD_SIZE = default("boardSize")
STARTING_MONEY = default("startingMoney")
SHED_CAPACITY = default("shedCapacity")
MAX_MARKET_ORDERS_PER_TURN = default("maxMarketOrdersPerTurn")
WEED_SPAWN_CHANCE = default("weedSpawnChance")
TOWN_SHOP_UNLOCK_INTERVAL = default("townShopUnlockInterval")
TOWN_SHOP_SELL_INTERVAL = default("townShopSellInterval")
TOWN_CENTER_SELL_INTERVAL = default("townCenterSellInterval")

DAYS = EPISODE_STEPS // TURNS_PER_DAY
QUADRANT_SIZE = BOARD_SIZE // 2

# --- genuinely ours -----------------------------------------------------------

# Not a constant in the env — it's an inline literal in _process_market's BUY_PRODUCT
# branch. Kept here so there's one place to fix if that branch ever widens.
BUYABLE_PRODUCTS = ("WHEAT", "FERTILIZER")

# Shops demanding a single product consume 2x per tick.
SINGLE_PRODUCT_SHOPS = frozenset(name for name, d in SHOPS.items() if len(d) == 1)


def cumulative_hire_cost(n, mult=FARM_HAND_COST_MULT):
    """Total cost of hiring n hands in one day."""
    return sum(hire_cost(i, mult) for i in range(n))
