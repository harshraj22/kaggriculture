"""Proves the extension contract: a new controller is one class + one registry entry.

If these break, "extensible" has quietly stopped being true.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentlib.controllers import (
    REGISTRY,
    ThresholdController,
    build_controller,
    register,
)
from agentlib.controllers.base import Controller
from agentlib.controllers.threshold import PREDICATES
from agentlib.game.observation import Obs
from agentlib.planner import Agent
from agentlib.settings import ConfigError, load_spec
from agentlib.strategies import build_all, default_strategy

KNOWN = {s.name for s in build_all()}


def obs_at(step=0, money=3000, opp_money=3000):
    farm = {"money": money, "tiles": [[None]], "farmer": [0, 0], "hands": [],
            "unlocked_quadrants": ["NW"], "hires_today": 0}
    opp = dict(farm, money=opp_money)
    return Obs({
        "player": 0, "step": step, "day": step // 24, "hour": step % 24,
        "farms": [farm, opp], "market": {"prices": {}, "inventory": {}},
        "town": {"unlocked_shops": []},
        "private": {"shed": {}, "seeds": {}, "inventories": [{}]},
    })


# --- the contract -------------------------------------------------------------


def test_every_registered_controller_implements_the_interface():
    for kind, cls in REGISTRY.items():
        assert issubclass(cls, Controller), kind
        assert cls.type == kind, f"{cls.__name__}.type must match its registry key"
        assert hasattr(cls, "from_spec") and hasattr(cls, "reset")


def test_dispatch_is_uniform_no_special_cases():
    """build_controller must go through from_spec for everything.

    A brand-new controller, never mentioned anywhere in agentlib, must build
    purely by being in the registry.
    """
    class Custom(Controller):
        type = "custom_test_only"

        def __init__(self, pick="safe_farmer"):
            self.pick = pick

        @classmethod
        def from_spec(cls, spec, known=None, strict=True):
            return cls(spec.get("pick", "safe_farmer"))

        def select(self, obs, candidates):
            return next((s for s in candidates if s.name == self.pick), None)

    register(Custom)
    try:
        c = build_controller({"type": "custom_test_only", "pick": "wheat_loop"}, KNOWN)
        assert isinstance(c, Custom)
        assert c.select(obs_at(), build_all()).name == "wheat_loop"
    finally:
        REGISTRY.pop("custom_test_only", None)


# --- stateful controllers -----------------------------------------------------


def test_agent_reset_clears_controller_state():
    """Without this, reusing an Agent leaks controller state across episodes —
    the same class of bug as the cached-agent one."""
    class Counter(Controller):
        type = "counter_test_only"

        def __init__(self):
            self.calls = 0

        def reset(self):
            self.calls = 0

        def select(self, obs, candidates):
            self.calls += 1
            return candidates[0] if candidates else None

    controller = Counter()
    agent = Agent(build_all(), controller, default_strategy())
    for step in range(3):
        agent.decide(obs_at(step).raw)
    assert controller.calls == 3

    agent.reset()
    assert controller.calls == 0, "Agent.reset() must reset the controller too"


def test_threshold_controller_carries_episode_state():
    spec = {
        "type": "threshold",
        "rules": [
            {"when": {"money_gte": 5000}, "strategy": "wheat_loop"},
            {"when": {}, "strategy": "safe_farmer"},
        ],
    }
    c = build_controller(spec, KNOWN, strict=True)
    strategies = build_all()

    assert c.select(obs_at(money=3000), strategies).name == "safe_farmer"
    assert c.select(obs_at(money=9000), strategies).name == "wheat_loop"
    assert c.diagnostics()["switches"] == 2, "state accumulates during the episode"
    assert c.diagnostics()["fires"] == [1, 1], "each rule matched exactly once"

    c.reset()
    assert c.diagnostics()["switches"] == 0
    assert c.diagnostics()["fires"] == [0, 0]

    assert "switches" not in c.describe(), (
        "describe() must be STATIC config: evaluate.py calls it on a parent-process "
        "controller that never plays a turn, so runtime counters there are always zero"
    )


# --- threshold logic ----------------------------------------------------------


def test_conditions_read_real_game_state_not_just_the_clock():
    spec = {
        "type": "threshold",
        "rules": [
            {"when": {"behind_by_gte": 1000}, "strategy": "wheat_loop"},
            {"when": {}, "strategy": "safe_farmer"},
        ],
    }
    c = build_controller(spec, KNOWN, strict=True)
    strategies = build_all()

    assert c.select(obs_at(money=3000, opp_money=9000), strategies).name == "wheat_loop"
    assert c.select(obs_at(money=9000, opp_money=3000), strategies).name == "safe_farmer"


def test_first_matching_rule_wins():
    spec = {
        "type": "threshold",
        "rules": [
            {"when": {"day_gte": 0}, "strategy": "wheat_loop"},
            {"when": {}, "strategy": "safe_farmer"},
        ],
    }
    c = build_controller(spec, KNOWN, strict=True)
    assert c.select(obs_at(0), build_all()).name == "wheat_loop"


def test_missing_catch_all_is_rejected():
    """State outside every rule silently plays the default — invisible in a sweep."""
    spec = {"type": "threshold", "rules": [{"when": {"day_gte": 25}, "strategy": "safe_farmer"}]}
    with pytest.raises(ConfigError, match="catch-all"):
        build_controller(spec, KNOWN, strict=True)


def test_unknown_condition_lists_what_is_available():
    spec = {
        "type": "threshold",
        "rules": [
            {"when": {"vibes_gte": 10}, "strategy": "safe_farmer"},
            {"when": {}, "strategy": "safe_farmer"},
        ],
    }
    with pytest.raises(ConfigError, match="vibes_gte"):
        build_controller(spec, KNOWN, strict=True)


def test_all_predicates_evaluate_without_raising():
    o = obs_at(100)
    for name, fn in PREDICATES.items():
        assert isinstance(fn(o), (int, float)), name


def test_shipped_threshold_config_builds():
    spec = load_spec(ROOT / "configs" / "threshold_demo.yaml", strict=True)
    assert isinstance(build_controller(spec, KNOWN, strict=True), ThresholdController)


# --- priority validation gained via from_spec ---------------------------------


def test_priority_order_is_validated_too():
    with pytest.raises(ConfigError, match="unknown strateg"):
        build_controller({"type": "priority", "order": ["nope"]}, KNOWN, strict=True)
