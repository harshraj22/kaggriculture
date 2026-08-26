"""Fast tests that don't need kaggle-environments installed."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agentlib.game import config, market
from agentlib.planner import decide, reset


def make_obs(**over):
    farm = {
        "money": 3000,
        "tiles": [[None] * 5 for _ in range(5)],
        "farmer": [2, 2],
        "hands": [],
        "unlocked_quadrants": ["NW"],
        "hires_today": 0,
    }
    obs = {
        "player": 0,
        "day": 0,
        "hour": 0,
        "farms": [farm, dict(farm)],
        "market": {"prices": {}, "inventory": {}},
        "town": {"unlocked_shops": []},
        "private": {"shed": {}, "seeds": {}, "inventories": [{}]},
    }
    obs.update(over)
    return obs


def test_agent_returns_valid_shape():
    reset()
    out = decide(make_obs())
    assert set(out) == {"farmer", "hands", "market"}
    assert isinstance(out["farmer"], list) and out["farmer"]


def test_agent_never_raises_on_garbage():
    reset()
    for junk in ({}, {"player": 0}, {"farms": []}, {"farms": [{}, {}], "player": 1}):
        out = decide(junk)
        assert out["farmer"], out


def test_market_order_cap():
    reset()
    obs = make_obs(private={"shed": {p: 5 for p in config.PRODUCTS}, "seeds": {}, "inventories": [{}]})
    out = decide(obs)
    assert len(out["market"]) <= config.MAX_MARKET_ORDERS_PER_TURN


def test_hire_cost_is_fibonacci():
    assert [config.hire_cost(i) for i in range(7)] == [1, 1, 2, 3, 5, 8, 13]
    assert config.cumulative_hire_cost(6) == 20  # 1+1+2+3+5+8


def test_land_order_is_fixed():
    """You don't choose which quadrant to buy next — the env fixes the order."""
    assert config.LAND_ORDER == ["NE", "SW", "SE"]
    assert config.LAND_PRICES == [1000, 2000, 4000]


@pytest.mark.parametrize(
    "res,expected_below,expected_above,expected_above2",
    [
        ("WHEAT", 45, 20, 19),
        ("CARROT", 42, 10, 1),
        ("TOMATO", 84, 24, 9),
        ("STRAWBERRY", 204, 1, 1),
        ("MELON", 300, 1, 1),
        ("EGG", 70, 40, 39),
        ("MILK", 256, 1, 1),
        ("WOOL", 240, 1, 1),
        ("FERTILIZER", 140, 60, 20),
    ],
)
def test_docs_price_table_matches_env(res, expected_below, expected_above, expected_above2):
    """The competition docs publish P(I0-T), P(I0+T), P(I0+2T).

    We no longer model the curve — this asserts the *documentation* still agrees
    with the installed env, so docs/GAME_SPEC.md can be trusted for strategy work.
    """
    p = config.MARKET_PARAMS[res]
    price, i0, t = config.market_price, p["I0"], p["T"]
    assert price(res, i0 - t) == expected_below
    assert price(res, i0 + t) == expected_above
    assert price(res, i0 + 2 * t) == expected_above2


def test_price_at_equilibrium_is_base():
    for res, p in config.MARKET_PARAMS.items():
        assert config.market_price(res, p["I0"]) == p["base"], res


def test_selling_depresses_price():
    """Straight from the env: adding inventory walks the price down."""
    inv = config.MARKET_I0
    assert config.market_price("MELON", inv + 50) < config.market_price("MELON", inv)


def test_dump_capacity_reflects_how_brutal_each_curve_is():
    """Premium goods have above_target > 1, so they floor after a modest glut;
    wheat's log curve absorbs almost anything. This is the asymmetry that makes
    sale timing matter for some products and not others."""
    inv = config.MARKET_I0
    melon = market.dump_capacity("MELON", inv)
    wheat = market.dump_capacity("WHEAT", inv)
    assert 0 < melon < wheat, f"melon={melon} wheat={wheat}"


def test_dump_capacity_terminates_at_the_price_floor():
    """Units sold at the $1 floor are not added to market inventory, so the price
    stops moving — the loop must not spin forever on an already-crashed market."""
    crashed = config.MARKET_I0 + 100_000
    assert config.market_price("MELON", crashed) == config.PRICE_FLOOR
    assert market.dump_capacity("MELON", crashed) <= 10_000
