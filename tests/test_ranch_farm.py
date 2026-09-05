"""Guards for the drain-sized herd.

As with `test_market_farm.py`, every test here corresponds to something that was
measured going wrong, not to something imagined. The three that matter most are
the priority ordering, the structure-kind matching and the last-day feed loop —
each of them silently emptied the ranch in a way no single function looks wrong.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentlib.game.config import ANIMALS, MARKET_I0, MARKET_PARAMS, SHOPS, market_price
from agentlib.game.market import base_price
from agentlib.game.observation import Obs, Tile
from agentlib.strategies import REGISTRY, build
from agentlib.strategies.ranch_farm import (
    ANIMAL_HARVEST,
    BUILD,
    CARE,
    FEED,
    PICKUP,
    PLACE,
    RanchFarm,
    animal_profile,
    unit_turns_per_day,
)

TURNS_PER_DAY = 24


def obs_at(day=5, hour=8, money=5000.0, hands=4, shed=None, shops=(),
           inventories=None, inventory=None, tiles=None):
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
        "private": {"shed": dict(shed or {}), "seeds": {},
                    "inventories": inventories or [{}]},
    })


def animal_tile(animal="SHEEP", yield_units=0, fed=True, cared=True,
                placed_day=0, fertilizer=False):
    return {"kind": ANIMALS[animal]["structure"], "animal": animal,
            "placed_day": placed_day, "yield_units": yield_units,
            "fed_today": fed, "cared_today": cared, "consecutive_unfed": 0,
            "fertilizer_available": fertilizer}


def board(*entries):
    """A 10x10 grid with NW unlocked; `entries` are (x, y, raw)."""
    tiles = [[None if x < 5 and y < 5 else "LOCKED" for x in range(10)]
             for y in range(10)]
    for x, y, raw in entries:
        tiles[y][x] = raw
    return tiles


# --- the engine-derived numbers ----------------------------------------------


def test_animal_profile_matches_the_engine():
    """Steady-state production is `min(max_held, 1 + interval)` per interval,
    because the care bonus accrues one per fed-and-cared day and is spent whole
    on the next production. Cross-checked against `_daily_refresh_animals` over
    24 days: sheep 30 units, cow 30, goose 44."""
    assert animal_profile("SHEEP") == (4 / 3, 6)
    assert animal_profile("COW") == (3 / 2, 8)
    assert animal_profile("GOOSE") == (2 / 1, 4)


def test_unit_turns_ranks_sheep_above_goose():
    """The correction that mattered: per TILE the three look close (267/240/100),
    per unit-turn they are 114/96/33, and a ranch is short of labour rather than
    land. Ranking per tile scored 2,434; the goose-free herd scored 23,330."""
    def per_turn(animal):
        rate, _ = animal_profile(animal)
        return rate * base_price(ANIMALS[animal]["product"]) / unit_turns_per_day(animal)

    assert per_turn("SHEEP") > per_turn("COW") > per_turn("GOOSE")
    assert per_turn("SHEEP") > 3 * per_turn("GOOSE")


def test_goose_is_out_of_the_default_mix():
    assert "GOOSE" not in RanchFarm.HERD_MIX
    assert set(RanchFarm.HERD_MIX) == {"SHEEP", "COW"}


# --- priority ordering, which killed a herd ----------------------------------


def test_pickup_outranks_care():
    """`FEED` spends wheat from the ACTING unit's inventory, so a unit holding
    none cannot feed — it falls through to CARE, which it can do, and is consumed
    there. Promoting CARE above PICKUP measured 13 animals on day 22 and **1 on
    day 23**: nobody hauled, every animal missed two days, and the engine escapes
    an animal at `consecutive_unfed >= 2`."""
    order = list(RanchFarm.PRIORITY)
    assert order.index(FEED) < order.index(PICKUP) < order.index(CARE)


def test_care_still_outranks_fertilizer():
    """CARE is worth ~0.96 extra units/day on a sheep (~190 coins); one COLLECT
    is worth one fertilizer, ~70 and falling."""
    order = list(RanchFarm.PRIORITY)
    assert order.index(CARE) < order.index("COLLECT_FERTILIZER")


# --- harvest timing ----------------------------------------------------------


def test_harvest_before_the_next_production_overflows():
    """Production is all-or-nothing at `min(max_held, 1 + interval)`, so an
    animal already holding one production's worth will lose the excess. The rule
    this replaces waited for `max_held - 1` and collected 18-20 units per sheep
    over 24 days against 30 for harvesting on sight."""
    obs = obs_at(day=12)
    assert RanchFarm._ready(obs, animal_tile("SHEEP", yield_units=0)) is False
    # A sheep makes 4 at a time into a cap of 6, so 3 already held will overflow.
    assert RanchFarm._ready(obs, animal_tile("SHEEP", yield_units=3)) is True
    assert RanchFarm._ready(obs, animal_tile("SHEEP", yield_units=2)) is False


def test_last_day_harvests_anything_held():
    obs = obs_at(day=29)
    assert obs.is_last_day
    assert RanchFarm._ready(obs, animal_tile("SHEEP", yield_units=1)) is True


# --- structures: a pasture is no use to a goose ------------------------------


def test_place_targets_only_matching_structures():
    """Emitting every vacant structure sent a goose-carrying unit to a pasture,
    where `_action_for` found no match and returned PASS — forever. Sixteen such
    assignments outranked CARE and HARVEST and the herd stopped being tended."""
    farm = RanchFarm()
    tiles = board(
        (0, 0, {"kind": "PASTURE"}),
        (1, 0, {"kind": "PASTURE"}),
        (2, 0, {"kind": "COOP"}),
    )
    obs = obs_at(tiles=tiles, inventories=[{"GOOSE": 1}, {}, {}, {}, {}])
    jobs = farm._classify(obs)
    assert jobs[PLACE] == [(2, 0)]


def test_no_place_targets_when_carrying_nothing():
    farm = RanchFarm()
    obs = obs_at(tiles=board((0, 0, {"kind": "PASTURE"})))
    assert farm._classify(obs)[PLACE] == []


def test_build_matches_the_waiting_animal():
    farm = RanchFarm()
    obs = obs_at(shed={"GOOSE": 1}, tiles=board())
    jobs = farm._classify(obs)
    assert jobs[BUILD], "a homeless goose should trigger a build"
    assert farm._action_for(BUILD, obs, 0, jobs[BUILD][0]) == ["BUILD_COOP"]

    obs = obs_at(shed={"SHEEP": 1}, tiles=board())
    jobs = farm._classify(obs)
    assert farm._action_for(BUILD, obs, 0, jobs[BUILD][0]) == ["BUILD_PASTURE"]


def test_no_build_when_a_matching_structure_is_already_vacant():
    """Building ahead of the herd is how the first version ended up with 17 empty
    pastures, a starved day-0 cow and a score of 809."""
    farm = RanchFarm()
    obs = obs_at(shed={"SHEEP": 1}, tiles=board((0, 0, {"kind": "PASTURE"})))
    assert farm._classify(obs)[BUILD] == []


def test_homeless_counts_per_structure_kind():
    farm = RanchFarm()
    obs = obs_at(shed={"GOOSE": 1}, tiles=board((0, 0, {"kind": "PASTURE"})))
    # A vacant pasture does not house a goose.
    assert farm._homeless(obs) == {"COOP": 1}


# --- feed --------------------------------------------------------------------


def test_no_feed_bought_on_the_last_day():
    """`_sell_quantity` dumps the shed on the last day, `_feed_wanted` sees an
    empty barn and rebuys, and the pair churns 24 times paying the spread.
    Measured: 695 wheat bought and 548 sold in one episode, on a herd of four."""
    farm = RanchFarm()
    obs = obs_at(day=29, tiles=board((0, 0, animal_tile("SHEEP"))))
    assert obs.is_last_day
    assert farm._feed_wanted(obs) == 0


def test_feed_target_is_capped_by_the_shed():
    """A thirty-animal herd at three days is ninety wheat of a hundred-unit shed,
    which starves the produce the feed exists to protect."""
    from agentlib.game.config import SHED_CAPACITY

    farm = RanchFarm()
    entries = [(x, y, animal_tile("SHEEP")) for y in range(5) for x in range(5)]
    obs = obs_at(day=5, tiles=board(*entries))
    assert farm._feed_wanted(obs) <= SHED_CAPACITY * farm.FEED_SHED_SHARE


def test_feed_is_not_sold_while_the_herd_needs_it():
    farm = RanchFarm()
    obs = obs_at(day=5, shed={"WHEAT": 20},
                 tiles=board(*[(x, 0, animal_tile("SHEEP")) for x in range(5)]))
    assert farm._sell_quantity(obs, "WHEAT", 20) == 0


def test_haul_scales_with_the_shortfall():
    """Unit inventories are wiped every evening, so every feeding unit needs a
    fresh pickup daily; confining hauling to a two-tile dawn window starved the
    herd outright."""
    farm = RanchFarm()
    entries = [(x, 0, animal_tile("SHEEP", fed=False)) for x in range(5)]
    entries += [(x, 1, animal_tile("SHEEP", fed=False)) for x in range(5)]
    obs = obs_at(day=5, hour=14, shed={"WHEAT": 40}, tiles=board(*entries))
    assert len(farm._haul_targets(obs)) >= 1

    fed = [(x, 0, animal_tile("SHEEP", fed=True)) for x in range(5)]
    assert farm._haul_targets(obs_at(shed={"WHEAT": 40}, tiles=board(*fed))) == []


# --- selling -----------------------------------------------------------------


def test_wool_is_metered_hard():
    """Wool is `sq/3.2`: 59 units in one order takes it from 200 to the 1-coin
    floor, and the drain is ~13 a day."""
    assert market_price("WOOL", MARKET_I0 + 59) <= 1
    farm = RanchFarm()
    obs = obs_at(day=5, shed={"WOOL": 80})
    assert 0 < farm._sell_quantity(obs, "WOOL", 80) < 59


def test_crashed_market_stops_receiving_stock():
    farm = RanchFarm()
    obs = obs_at(day=5, shed={"WOOL": 40}, inventory={"WOOL": MARKET_I0 + 200})
    assert farm._sell_quantity(obs, "WOOL", 40) == 0


def test_livestock_is_never_sold():
    farm = RanchFarm()
    obs = obs_at(day=5, shed={"SHEEP": 2})
    assert farm._sell_quantity(obs, "SHEEP", 2) == 0
    assert farm._sell_quantity(obs_at(day=29, shed={"SHEEP": 2}), "SHEEP", 2) == 0


def test_last_day_dumps_produce():
    farm = RanchFarm()
    obs = obs_at(day=29, shed={"WOOL": 80}, inventory={"WOOL": MARKET_I0 + 200})
    assert farm._sell_quantity(obs, "WOOL", 80) == 80


# --- the plan ----------------------------------------------------------------


def test_herd_plan_is_bounded_by_labour_not_just_land():
    farm = RanchFarm()
    for hands in (0, 2, 8):
        obs = obs_at(hands=hands, money=100_000, shops=list(SHOPS))
        plan = farm._herd_plan(obs)
        assert sum(plan.values()) <= int((1 + hands) * farm.ANIMALS_PER_UNIT), hands


def test_herd_plan_puts_sheep_first():
    farm = RanchFarm()
    obs = obs_at(hands=8, money=100_000, shops=list(SHOPS))
    plan = farm._herd_plan(obs)
    assert plan, "expected a herd"
    assert next(iter(plan)) == "SHEEP"


def test_herd_plan_caps_each_animal_near_its_drain():
    """Ten sheep is what the town's wool appetite supports; the eleventh sells
    into a curve that reaches the floor 59 units above I0."""
    farm = RanchFarm()
    obs = obs_at(hands=12, money=100_000, shops=list(SHOPS))
    assert farm._herd_plan(obs).get("SHEEP", 0) <= 14


def test_lead_time_is_checked_per_animal():
    """A goose yields on day 4 and a cow on day 8, so one global cutoff either
    wastes the last week of goose income or buys cows that never produce."""
    farm = RanchFarm()
    farm.HERD_MIX = ("SHEEP", "COW", "GOOSE")
    late = obs_at(day=int(30 - ANIMALS["COW"]["first_yield_day"]), hands=8,
                  money=100_000, shops=list(SHOPS))
    assert "COW" not in farm._wanted(late)


def test_wanted_nets_off_animals_already_in_transit():
    """Livestock bought sits in the shed until a unit carries and places it.
    Missing that double-buys the whole herd."""
    farm = RanchFarm()
    bare = obs_at(hands=8, money=100_000, shops=list(SHOPS))
    with_stock = obs_at(hands=8, money=100_000, shops=list(SHOPS),
                        shed={"SHEEP": 3})
    assert farm._wanted(with_stock).get("SHEEP", 0) == \
        max(0, farm._wanted(bare).get("SHEEP", 0) - 3)


# --- contract ----------------------------------------------------------------


def test_registered_and_grows_nothing():
    assert "ranch_farm" in REGISTRY
    farm = build("ranch_farm")
    assert farm._seed_targets(obs_at(), {}) == {}


def test_act_returns_a_legal_plan():
    from agentlib.game.actions import validate

    farm = RanchFarm()
    for day in (0, 15, 29):
        obs = obs_at(day=day, hands=3, tiles=board((0, 0, animal_tile("SHEEP"))))
        plan = validate(farm.act(obs), n_hands=3)
        assert len(plan["hands"]) == 3


def test_every_searched_param_exists():
    """Ties tools/optimize.py's `ranch_farm` space to the class."""
    searched = {
        "HERD_MIX", "HERD_SATURATION", "SELL_FLOOR_RATIO", "SHED_PRESSURE",
        "FEED_DAYS_BUFFER", "FEED_SHED_SHARE", "FEED_CARRY", "MAX_QUADRANTS",
        "MAX_HANDS", "HANDS_PER_ANIMAL", "ANIMALS_PER_UNIT",
        "CASH_RESERVE_FOR_HERD", "HERD_LEAD_SLACK",
    }
    for name in searched:
        assert hasattr(RanchFarm, name), name


def _unused():  # pragma: no cover - keeps the imports honest
    return Tile, ANIMAL_HARVEST
