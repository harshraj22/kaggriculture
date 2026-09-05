"""Guards for the drain-sized portfolio.

Every test here corresponds to a bug that was measured, not imagined. The two
that matter most — `_seed_targets` returning stock levels rather than increments,
and `_deficits` preserving value order — each cost a whole bankrupt episode
before they were understood, and neither is visible from reading one function.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentlib.game.config import CROPS, MARKET_I0, MARKET_PARAMS, SHOPS, market_price
from agentlib.game.market import (
    base_price,
    depth_to_price,
    drain_per_day,
    projected_drain_per_day,
)
from agentlib.game.observation import Obs
from agentlib.strategies import REGISTRY, build
from agentlib.strategies.market_farm import MarketFarm, yield_profile
from agentlib.strategies.wheat_farm import PLANT

TURNS_PER_DAY = 24


def obs_at(day=0, hour=8, money=3000.0, hands=2, shed=None, seeds=None,
           shops=(), inventory=None, tiles=None):
    """A 10x10 board with the NW quadrant unlocked and everything else LOCKED."""
    if tiles is None:
        tiles = [[None if x < 5 and y < 5 else "LOCKED" for x in range(10)]
                 for y in range(10)]
    farm = {
        "money": money, "tiles": tiles, "farmer": [0, 0],
        "hands": [[1, 1]] * hands, "unlocked_quadrants": ["NW"], "hires_today": 0,
    }
    inv = {item: MARKET_I0 for item in MARKET_PARAMS}
    inv.update(inventory or {})
    return Obs({
        "player": 0, "step": day * TURNS_PER_DAY + hour, "day": day, "hour": hour,
        "farms": [farm, dict(farm)],
        "market": {"inventory": inv,
                   "prices": {k: market_price(k, v) for k, v in inv.items()}},
        "town": {"unlocked_shops": list(shops)},
        "private": {"shed": dict(shed or {}), "seeds": dict(seeds or {}),
                    "inventories": [{}]},
    })


def plant_tile(crop, planted_day=0, yield_units=1, watered=True):
    return {"kind": "PLANT", "crop": crop, "planted_day": planted_day,
            "watered_today": watered, "consecutive_unwatered": 0,
            "yield_units": yield_units, "fertilized_until_day": -1}


# --- the engine-derived numbers ----------------------------------------------


def test_yield_profile_matches_the_engine_tables():
    """One-time crops saturate on yield, ongoing crops on production COUNT.

    The two families count so differently that hand-copied numbers were wrong
    twice. Melon is the case that matters: it reaches its 6 units at age 10
    against a `max_yield_day` of 12, so anything holding to the calendar burns
    two tile-days per melon.
    """
    assert yield_profile("WHEAT") == (4 / 5, 5)
    assert yield_profile("CARROT") == (3 / 4, 4)
    assert yield_profile("MELON") == (6 / 11, 11)
    assert yield_profile("TOMATO") == (4 / 12, 12)
    assert yield_profile("STRAWBERRY") == (4 / 17, 17)


def test_every_crop_in_the_mix_has_a_positive_finite_rate():
    for crop in MarketFarm.MIX:
        rate, days = yield_profile(crop)
        assert 0 < rate <= 1, crop
        assert 0 < days <= 40, crop


def test_drain_counts_single_product_shops_double():
    """YARN_STORE demands only WOOL, so the engine has it consume 2 per tick.
    That doubling is the entire reason wool is the most valuable good on the
    board, so it is worth a test of its own."""
    assert "YARN_STORE" in SHOPS and SHOPS["YARN_STORE"] == ["WOOL"]
    ticks = TURNS_PER_DAY / 4
    # WOOL is also a town-centre product, hence the +1/day.
    assert drain_per_day("WOOL", ["YARN_STORE"]) == 2 * ticks + 1
    assert drain_per_day("CARROT", ["PET_CAFE"]) == 2 * ticks + 1
    # PIZZA_SHOP demands three products, so one each.
    assert drain_per_day("MILK", ["PIZZA_SHOP"]) == 1 * ticks + 1


def test_repeated_shop_instances_each_drink_independently():
    """Shops are drawn WITH REPLACEMENT, so three yarn stores is three times the
    wool demand — which is why the drain reads the live list, not the table."""
    one = drain_per_day("WOOL", ["YARN_STORE"])
    three = drain_per_day("WOOL", ["YARN_STORE"] * 3)
    assert three == 3 * (one - 1) + 1


def test_melon_has_no_shop_demand_at_all():
    """The finding that reframed the whole strategy: melon's only consumer is the
    town centre, at one unit a day, against a base price of 250 and the harshest
    glut curve in the game."""
    assert not any("MELON" in products for products in SHOPS.values())
    assert drain_per_day("MELON", list(SHOPS)) == 1.0
    assert MARKET_PARAMS["MELON"]["above_func"] == "sq"


def test_fertilizer_drain_is_exactly_zero():
    """Nothing consumes fertilizer. Every unit sold sits in the market forever,
    so its price never recovers — a fact any future animal strategy needs."""
    assert drain_per_day("FERTILIZER", list(SHOPS)) == 0.0


def test_projected_drain_exceeds_todays_early_and_converges_late():
    """Sizing off today's drain alone cripples the opening: on day 0 no shop has
    unlocked, so every crop's plan would be one tile."""
    early = projected_drain_per_day("WHEAT", [], days_left=30)
    assert early > drain_per_day("WHEAT", [])
    # With the roster full and the season over there is nothing left to project.
    full = list(SHOPS)
    assert projected_drain_per_day("WHEAT", full, days_left=0) == \
        drain_per_day("WHEAT", full)


# --- the sell throttle -------------------------------------------------------


def test_depth_to_price_is_absolute_not_relative():
    """The bug `dump_capacity` has and this does not: a market already crashed
    to 3% of base must stop receiving stock, not receive half as much."""
    crashed = MARKET_I0 + 400  # melon at 400 over I0 is deep into the floor
    assert market_price("MELON", crashed) < 0.5 * base_price("MELON")
    assert depth_to_price("MELON", crashed, 0.7 * base_price("MELON")) == 0


def test_depth_to_price_counts_units_at_or_above_the_target():
    """Off-by-one guard: the unit sold AT the last good offset still counts."""
    item = "STRAWBERRY"
    target = 0.7 * base_price(item)
    n = depth_to_price(item, MARKET_I0, target)
    assert n >= 1
    assert market_price(item, MARKET_I0 + n - 1) >= target
    assert market_price(item, MARKET_I0 + n) < target


def test_depth_to_price_terminates_at_the_price_floor():
    """A target under PRICE_FLOOR is satisfiable forever; the search must cap
    rather than hang."""
    assert depth_to_price("WHEAT", MARKET_I0, 0.0) >= 10_000


def test_last_day_dumps_everything_regardless_of_price():
    farm = MarketFarm()
    obs = obs_at(day=29, inventory={"MELON": MARKET_I0 + 400})
    assert obs.is_last_day
    assert farm._sell_quantity(obs, "MELON", 40) == 40


def test_shed_pressure_releases_only_the_excess():
    """Overflow is DISCARDED at end of day, so zero is the alternative price and
    some forced selling is right — but only the excess. Returning the whole shelf
    silently overrode the throttle sitting next to it and crashed a market to
    free a handful of slots."""
    from agentlib.game.config import SHED_CAPACITY

    farm = MarketFarm()
    crashed = {"MELON": MARKET_I0 + 400}
    calm = obs_at(day=5, shed={"MELON": 10}, inventory=crashed)
    assert farm._sell_quantity(calm, "MELON", 10) == 0

    packed = obs_at(day=5, shed={"MELON": 95}, inventory=crashed)
    expected = int(95 - farm.SHED_PRESSURE * SHED_CAPACITY)
    assert farm._sell_quantity(packed, "MELON", 95) == expected
    assert 0 < expected < 95


# --- the plan ----------------------------------------------------------------


def test_plan_never_exceeds_the_plant_budget():
    farm = MarketFarm()
    for hands in (0, 2, 8):
        obs = obs_at(hands=hands, money=20_000)
        budget = int((1 + hands) * farm.MAX_PLANTS_PER_UNIT)
        assert sum(farm._plan(obs).values()) <= budget, hands


def test_plan_caps_melon_near_its_drain_not_its_tile_value():
    """Melon is worth 129 coins per tile-day, six times anything else, and the
    plan must still refuse to grow more than ~2 tiles of it. This is the whole
    thesis of the strategy in one assertion: `melon_farm` held ten."""
    farm = MarketFarm()
    obs = obs_at(day=5, hands=8, money=50_000, shops=list(SHOPS))
    assert farm._plan(obs).get("MELON", 0) <= 3


def test_plan_skips_crops_that_cannot_mature():
    farm = MarketFarm()
    obs = obs_at(day=28, hands=8, money=50_000, shops=list(SHOPS))
    plan = farm._plan(obs)
    for crop in ("STRAWBERRY", "MELON", "TOMATO"):
        assert crop not in plan, crop
    # Wheat still can: first_yield_day 2 inside the remaining days.
    assert CROPS["WHEAT"]["first_yield_day"] == 2


def test_capital_weighting_prefers_cheap_fast_seed_when_broke():
    """The measured bankruptcy. Per tile-day strawberry (22.35) outranks wheat
    (18.00), so an opening plan ranked that way spends 2,700 of a 3,000 purse on
    seed that pays nothing for ten days. Per coin of seed per day the order
    inverts, and that is the ranking a broke farm needs."""
    farm = MarketFarm()
    broke = obs_at(day=0, hands=8, money=3000.0)
    assert farm._score(broke, "WHEAT") > farm._score(broke, "STRAWBERRY")

    rich = obs_at(day=8, hands=8, money=60_000.0)
    assert farm._score(rich, "STRAWBERRY") > farm._score(rich, "WHEAT")


def test_capital_ease_recovers_the_pure_tile_ranking():
    """Above CAPITAL_EASE the blend must vanish exactly, so the late-game
    ranking is the undistorted coins-per-tile-day it should be."""
    farm = MarketFarm()
    obs = obs_at(money=farm.CAPITAL_EASE * 2)
    for crop in farm.MIX:
        rate, cycle = yield_profile(crop)
        expected = rate * farm._price(obs, crop) - CROPS[crop]["seed"] / cycle
        assert farm._score(obs, crop) == expected, crop


# --- the two bugs that cost an episode each ----------------------------------


def test_seed_targets_are_stock_levels_not_increments():
    """`_market` buys `target - held`, so seed already in hand must suppress the
    order. Returning the tile shortfall instead made the farm re-buy the same
    shortfall every turn — 11 melon and 55 wheat seeds by the end of day 0, and
    306 coins left of 3,000.
    """
    farm = MarketFarm()
    jobs = {PLANT: [(x, 0) for x in range(5)]}

    bare = obs_at(day=0, hands=8, money=3000.0)
    wanted = farm._seed_targets(bare, jobs)
    assert wanted, "expected the plan to ask for some seed"

    crop, target = next(iter(wanted.items()))
    stocked = obs_at(day=0, hands=8, money=3000.0, seeds={crop: target + 50})
    # Same tile shortfall, but the seed is already in the barn: `_market`
    # computes target - held, which must now be <= 0.
    assert farm._seed_targets(stocked, jobs).get(crop, 0) - (target + 50) <= 0


def test_deficits_keep_plan_order_not_shortfall_order():
    """Sorting by size of gap inverts the ranking `_plan` just computed:
    strawberry is short by dozens of tiles and wheat by a few, so the largest
    gap is always the crop we decided we wanted least."""
    farm = MarketFarm()
    obs = obs_at(day=0, hands=8, money=3000.0, shops=list(SHOPS))
    plan_order = list(farm._plan(obs))
    deficit_order = [crop for _short, crop in farm._deficits(obs, {})]
    assert deficit_order == [c for c in plan_order if c in deficit_order]


def test_seed_targets_never_exceed_the_plantable_tiles():
    farm = MarketFarm()
    obs = obs_at(day=0, hands=8, money=50_000, shops=list(SHOPS))
    for n_empty in (0, 1, 5):
        jobs = {PLANT: [(x, 0) for x in range(n_empty)]}
        assert sum(farm._seed_targets(obs, jobs).values()) <= n_empty


# --- harvest timing ----------------------------------------------------------


def test_ongoing_crops_are_held_until_full():
    """An ongoing plant yields `max_yield` units in total however often it is
    picked, so harvesting on sight is four trips where one would do — and travel
    is ~60% of measured unit-turns."""
    obs = obs_at(day=12)
    partial = plant_tile("STRAWBERRY", planted_day=0, yield_units=1)
    full = plant_tile("STRAWBERRY", planted_day=0, yield_units=4)
    assert MarketFarm._ripe(obs, _tile(partial)) is False
    assert MarketFarm._ripe(obs, _tile(full)) is True


def test_ongoing_crops_are_harvested_before_they_decay():
    """`max_lifespan_step` is set the moment the last production lands, after
    which the engine eats a unit every other step. Holding for a fuller load
    past that point loses stock outright."""
    spec = CROPS["STRAWBERRY"]
    last = spec["first_yield_day"] + (spec["max_yield"] - 1) * spec["interval"]
    tile = _tile(plant_tile("STRAWBERRY", planted_day=0, yield_units=1))
    assert MarketFarm._ripe(obs_at(day=last - 1), tile) is False
    assert MarketFarm._ripe(obs_at(day=last), tile) is True


def test_one_time_crops_keep_the_base_harvest_rule():
    """Melon must still be picked at saturation (age 10), not at `max_yield_day`
    (12) — the inherited fix, which this override must not undo."""
    from agentlib.strategies.wheat_farm import WheatFarm

    for age, units in ((9, 5), (10, 6), (12, 6)):
        tile = _tile(plant_tile("MELON", planted_day=0, yield_units=units))
        assert MarketFarm._ripe(obs_at(day=age), tile) is \
            WheatFarm._ripe(obs_at(day=age), tile)


def _tile(raw):
    from agentlib.game.observation import Tile

    return Tile(0, 0, raw)


# --- registration ------------------------------------------------------------


def test_registered_and_constructible():
    assert "market_farm" in REGISTRY
    assert build("market_farm").name == "market_farm"


def test_absent_from_default_order():
    """Unmeasured strategies must not silently change what `priority` plays."""
    from agentlib.strategies import DEFAULT_ORDER

    assert "market_farm" not in DEFAULT_ORDER


def test_act_returns_a_legal_plan_on_a_bare_board():
    from agentlib.game.actions import validate

    farm = MarketFarm()
    for day in (0, 15, 29):
        obs = obs_at(day=day, hands=3)
        plan = validate(farm.act(obs), n_hands=3)
        assert len(plan["hands"]) == 3


def test_act_respects_a_unit_allocation():
    """The controller may hand this strategy a subset of the crew; planning for
    anyone else's unit silently drops their work."""
    farm = MarketFarm()
    obs = obs_at(hands=4, seeds={"WHEAT": 10})
    plan = farm.act(obs, units=[0, 1])
    assert plan["hands"][2] == ["PASS"]
    assert plan["hands"][3] == ["PASS"]


# --- the params channel ------------------------------------------------------


def test_params_override_class_defaults_per_instance():
    """Instance attributes, not class attributes: both seats share the class
    object inside one interpreter, so writing to it would be a global, not a
    config."""
    from agentlib.strategies import apply_params, build_all

    a = build_all()
    b = build_all()
    apply_params(a, {"market_farm": {"SATURATION": 2.0}}, strict=True)
    by_a = {s.name: s for s in a}
    by_b = {s.name: s for s in b}
    assert by_a["market_farm"].SATURATION == 2.0
    assert by_b["market_farm"].SATURATION == MarketFarm.SATURATION


def test_params_reject_unknown_names_when_strict():
    """The quietest possible failure mode: a misspelt param means every trial of
    a sweep is identical and the flat surface reads as a finding."""
    import pytest

    from agentlib.settings import ConfigError
    from agentlib.strategies import apply_params, build_all

    with pytest.raises(ConfigError):
        apply_params(build_all(), {"market_farm": {"SATURATIN": 2.0}}, strict=True)
    with pytest.raises(ConfigError):
        apply_params(build_all(), {"no_such_farm": {"X": 1}}, strict=True)


def test_params_tolerate_bad_names_when_lenient():
    """Inside an episode a bad param must degrade play, never end it."""
    from agentlib.strategies import apply_params, build_all

    assert apply_params(build_all(), {"nope": {"X": 1}}, strict=False) == []


def test_params_refuse_private_and_callable_targets():
    import pytest

    from agentlib.settings import ConfigError
    from agentlib.strategies import apply_params, build_all

    for bad in ("_plan", "act", "name"):
        with pytest.raises(ConfigError):
            apply_params(build_all(), {"market_farm": {bad: 1}}, strict=True)


def test_every_searched_param_exists_on_the_strategy():
    """Ties tools/optimize.py's `market_farm` space to the class. Renaming a
    constant without updating the space is otherwise invisible until the sweep
    has burned an afternoon."""
    searched = {
        "SATURATION", "SELL_FLOOR_RATIO", "SHED_PRESSURE", "CAPITAL_WEIGHT",
        "CAPITAL_EASE", "LEAD_SLACK_DAYS", "MAX_QUADRANTS", "MAX_HANDS",
        "HANDS_PER_TILE", "MAX_PLANTS_PER_UNIT", "CASH_RESERVE",
    }
    for name in searched:
        assert hasattr(MarketFarm, name), name
