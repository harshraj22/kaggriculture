"""Contract tests for the strategy/controller/arbiter wiring."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agentlib import planner
from agentlib.actions import DISPOSAL, PROCUREMENT, TurnPlan, validate
from agentlib.config import MAX_MARKET_ORDERS_PER_TURN
from agentlib.controllers import PriorityController
from agentlib.planner import MAX_STRIKES, Agent
from agentlib.strategies import SafeFarmer, build_all, default_strategy
from agentlib.strategy import Strategy


def make_obs(step=0, **over):
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
        "day": step // 24,
        "hour": step % 24,
        "step": step,
        "farms": [farm, dict(farm)],
        "market": {"prices": {}, "inventory": {}},
        "town": {"unlocked_shops": []},
        "private": {"shed": {}, "seeds": {}, "inventories": [{}]},
    }
    obs.update(over)
    return obs


class Spy(Strategy):
    """Records every hook call so we can assert the lifecycle."""

    def __init__(self, name="spy", eligible=True, fail_act=False):
        self.name = name
        self._eligible = eligible
        self.fail_act = fail_act
        self.observed = 0
        self.acted = 0
        self.notified = []

    def observe(self, obs):
        self.observed += 1

    def is_eligible(self, obs):
        return self._eligible

    def act(self, obs):
        self.acted += 1
        if self.fail_act:
            raise RuntimeError("boom")
        return {"farmer": ["PASS"], "hands": [], "market": []}

    def on_action(self, obs, action, chosen):
        self.notified.append(chosen)


def build(*strategies, order=()):
    return Agent(list(strategies), PriorityController(order), default_strategy())


# --- lifecycle ---------------------------------------------------------------


def test_every_strategy_observes_even_when_not_selected():
    a, b = Spy("a"), Spy("b")
    agent = build(a, b, order=["a"])
    agent.decide(make_obs())

    assert a.observed == b.observed == 1
    assert a.acted == 1 and b.acted == 0, "only the selected strategy acts"


def test_every_strategy_is_told_who_actually_acted():
    a, b = Spy("a"), Spy("b")
    agent = build(a, b, order=["a"])
    agent.decide(make_obs())

    assert a.notified == ["a"]
    assert b.notified == ["a"], "unselected strategies learn the turn was not theirs"


def test_controller_respects_priority_order():
    a, b = Spy("a"), Spy("b")
    agent = build(a, b, order=["b", "a"])
    agent.decide(make_obs())
    assert b.acted == 1 and a.acted == 0


def test_ineligible_strategies_are_masked_out():
    a, b = Spy("a", eligible=False), Spy("b")
    agent = build(a, b, order=["a", "b"])
    agent.decide(make_obs())
    assert a.acted == 0 and b.acted == 1


# --- reselection -------------------------------------------------------------


def test_controller_is_consulted_every_turn():
    """No stickiness in the arbiter: a schedule controller's boundaries must be
    exact, so holding a selection for N turns would push them off by up to N."""
    a, b = Spy("a"), Spy("b")
    agent = build(a, b, order=["a"])

    for step in range(5):
        agent.decide(make_obs(step))
    assert a.acted == 5

    a._eligible = False
    agent.decide(make_obs(5))
    assert b.acted == 1, "switch takes effect on the very next turn"


def test_holder_is_dropped_immediately_when_ineligible():
    a, b = Spy("a"), Spy("b")
    agent = build(a, b, order=["a", "b"])
    agent.decide(make_obs(0))
    assert a.acted == 1

    a._eligible = False
    agent.decide(make_obs(1))  # mid-hold, but the holder is no longer eligible
    assert b.acted == 1


# --- failure handling --------------------------------------------------------


def test_failing_strategy_falls_back_to_default():
    bad = Spy("bad", fail_act=True)
    agent = build(bad, order=["bad"])
    action = agent.decide(make_obs())

    assert action["farmer"], "a legal action came back despite the failure"
    assert agent.journal[-1][1] == SafeFarmer.name, "default was credited, not the failure"
    assert agent.strikes["bad"] == 1


def test_strategy_is_disabled_after_max_strikes():
    bad, good = Spy("bad", fail_act=True), Spy("good")
    agent = build(bad, good, order=["bad", "good"])

    for step in range(MAX_STRIKES):
        agent.decide(make_obs(step))
    assert agent.strikes["bad"] == MAX_STRIKES

    before = bad.acted
    agent.decide(make_obs(MAX_STRIKES))
    assert bad.acted == before, "disabled strategy is never asked to act again"
    assert bad.observed == MAX_STRIKES, "and stops being observed too"


def test_default_strategy_is_never_disabled():
    agent = build(order=[])
    for _ in range(MAX_STRIKES + 2):
        agent.decide(make_obs())
    assert agent.strikes.get(SafeFarmer.name, 0) == 0
    assert not agent._disabled(agent.default)


def test_broken_observe_does_not_change_who_acts():
    class BadObserver(Spy):
        def observe(self, obs):
            raise RuntimeError("boom")

    bad, good = BadObserver("bad"), Spy("good")
    agent = build(bad, good, order=["bad"])
    action = agent.decide(make_obs())

    assert bad.acted == 1, "a broken observe() is contained, not escalated"
    assert action["farmer"]


def test_module_entrypoint_survives_garbage():
    planner.reset()
    for junk in ({}, {"player": 0}, {"farms": []}, {"farms": [{}, {}], "player": 1}):
        out = planner.decide(junk)
        assert set(out) == {"farmer", "hands", "market"}
        assert out["farmer"]


# --- action validation -------------------------------------------------------


def test_validate_rejects_unusable_shapes():
    for junk in (None, [], "PASS", {"hands": 3}, {"market": 7}):
        with pytest.raises((TypeError, ValueError)):
            validate(junk)


def test_validate_treats_a_missing_farmer_as_pass():
    """An absent or empty farmer key means 'do nothing', not 'crash'."""
    for lenient in ({}, {"farmer": None}, {"farmer": []}):
        assert validate(lenient)["farmer"] == ["PASS"]


def test_validate_coerces_and_caps():
    out = validate({"farmer": "PASS", "hands": ["WATER"], "market": [["SELL", "WHEAT", 1]] * 50})
    assert out["farmer"] == ["PASS"]
    assert out["hands"] == [["WATER"]]
    assert len(out["market"]) == MAX_MARKET_ORDERS_PER_TURN


def test_procurement_outranks_disposal_at_the_cap():
    plan = TurnPlan()
    for _ in range(MAX_MARKET_ORDERS_PER_TURN):
        plan.order("SELL", "WHEAT", 1, priority=DISPOSAL)
    plan.order("BUY_SEED", "MELON", 1, priority=PROCUREMENT)

    market = plan.to_dict()["market"]
    assert len(market) == MAX_MARKET_ORDERS_PER_TURN
    assert market[0] == ["BUY_SEED", "MELON", 1], "procurement survives truncation"


# --- registry ----------------------------------------------------------------


def test_all_registered_strategies_produce_legal_actions():
    obs = make_obs()
    for s in build_all():
        s.on_episode_start()
        s.observe(planner.Obs(obs))
        out = validate(s.act(planner.Obs(obs)))
        assert out["farmer"], s.name


def test_stateless_strategy_needs_only_act():
    class Minimal(Strategy):
        name = "minimal"

        def act(self, obs):
            return {"farmer": ["PASS"], "hands": [], "market": []}

    agent = build(Minimal(), order=["minimal"])
    assert agent.decide(make_obs())["farmer"] == ["PASS"]
