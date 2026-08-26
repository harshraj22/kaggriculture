"""Config loading, controller construction, and the guarantees the sweep relies on."""

import json
import math
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentlib.controllers import (
    PriorityController,
    ScheduleController,
    build_controller,
)
from agentlib.observation import Obs
from agentlib.settings import (
    ENV_CONFIG,
    ENV_CONTROLLER,
    ConfigError,
    load_spec,
    spec_hash,
)
from agentlib.strategies import build_all

KNOWN = {s.name for s in build_all()}
FULL_SEASON = {"from_day": 0, "to_day": 29}


@pytest.fixture(autouse=True)
def _clean_env():
    saved = {k: os.environ.pop(k, None) for k in (ENV_CONFIG, ENV_CONTROLLER)}
    yield
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def obs_at(step):
    farm = {"money": 3000, "tiles": [[None]], "farmer": [0, 0], "hands": []}
    return Obs({
        "player": 0, "step": step, "day": step // 24, "hour": step % 24,
        "farms": [farm, dict(farm)], "market": {"prices": {}, "inventory": {}},
        "town": {"unlocked_shops": []},
        "private": {"shed": {}, "seeds": {}, "inventories": [{}]},
    })


# --- loading ------------------------------------------------------------------


def test_no_config_gives_the_builtin_controller():
    spec = load_spec(None, strict=True)
    assert spec["type"] == "priority"
    assert isinstance(build_controller(spec, KNOWN), PriorityController)


def test_env_var_supplies_the_config():
    os.environ[ENV_CONFIG] = str(ROOT / "configs" / "safe_only.yaml")
    assert load_spec(strict=True)["type"] == "schedule"


def test_controller_env_var_overrides_the_file():
    """Lets one config be re-run under a different controller without editing it."""
    os.environ[ENV_CONTROLLER] = "priority"
    spec = load_spec(ROOT / "configs" / "safe_only.yaml", strict=True)
    assert spec["type"] == "priority"


def test_missing_config_is_fatal_under_strict_and_survivable_otherwise():
    with pytest.raises(ConfigError):
        load_spec("configs/does_not_exist.yaml", strict=True)
    assert load_spec("configs/does_not_exist.yaml", strict=False)["type"] == "priority"


def test_shipped_configs_all_load_and_build():
    for path in sorted((ROOT / "configs").glob("*.yaml")):
        controller = build_controller(load_spec(path, strict=True), KNOWN, strict=True)
        assert controller.describe()["type"], path.name


# --- hashing ------------------------------------------------------------------


def test_hash_follows_content_not_filename():
    a = {"type": "schedule", "schedule": [{**FULL_SEASON, "strategy": "safe_farmer"}]}
    b = dict(a)
    b["_source"] = "somewhere/else.yaml"
    assert spec_hash(a) == spec_hash(b), "_source must not affect identity"

    c = {"type": "schedule", "schedule": [{**FULL_SEASON, "strategy": "wheat_loop"}]}
    assert spec_hash(a) != spec_hash(c)


# --- schedule semantics -------------------------------------------------------


def test_day_bounds_are_inclusive_and_first_match_wins():
    spec = {
        "type": "schedule",
        "schedule": [
            {"from_day": 0, "to_day": 9, "strategy": "wheat_loop"},
            {"from_day": 0, "to_day": 29, "strategy": "safe_farmer"},
        ],
    }
    c = build_controller(spec, KNOWN, strict=True)
    strategies = build_all()

    assert c.select(obs_at(0), strategies).name == "wheat_loop"
    assert c.select(obs_at(9 * 24 + 23), strategies).name == "wheat_loop", "day 9 fully covered"
    assert c.select(obs_at(10 * 24), strategies).name == "safe_farmer", "day 10 switches"


def test_scheduled_but_ineligible_defers_to_the_caller():
    spec = {"type": "schedule", "schedule": [{**FULL_SEASON, "strategy": "wheat_loop"}]}
    c = build_controller(spec, KNOWN, strict=True)
    only_safe = [s for s in build_all() if s.name == "safe_farmer"]
    assert c.select(obs_at(0), only_safe) is None, "None means 'use the code default'"


def test_unknown_strategy_is_fatal_under_strict():
    spec = {"type": "schedule", "schedule": [{**FULL_SEASON, "strategy": "nope"}]}
    with pytest.raises(ConfigError, match="unknown strategy"):
        build_controller(spec, KNOWN, strict=True)

    # Lenient mode degrades gracefully instead: the rule simply never matches, so
    # select() defers to the code default. Losing one rule beats losing the episode.
    lenient = build_controller(spec, KNOWN, strict=False)
    assert isinstance(lenient, ScheduleController)
    assert lenient.select(obs_at(0), build_all()) is None


def test_gaps_are_rejected():
    """An uncovered range silently plays the default — an invisible confound in a sweep."""
    spec = {
        "type": "schedule",
        "schedule": [
            {"from_day": 0, "to_day": 9, "strategy": "safe_farmer"},
            {"from_day": 20, "to_day": 29, "strategy": "safe_farmer"},
        ],
    }
    with pytest.raises(ConfigError, match="uncovered"):
        build_controller(spec, KNOWN, strict=True)


def test_malformed_rules_are_rejected():
    def bad(rule):
        return {"type": "schedule", "schedule": [rule]}

    for rule in (
        {"from_day": 0, "to_day": 29},                                   # no strategy
        {"from_day": 10, "to_day": 2, "strategy": "safe_farmer"},        # inverted
        {"from_day": 0, "to_turn": 5, "strategy": "safe_farmer"},        # mixed units
        {"from_day": 0, "to_day": 29, "strategy": "safe_farmer", "x": 1},  # typo'd key
    ):
        with pytest.raises(ConfigError):
            build_controller(bad(rule), KNOWN, strict=True)


def test_empty_schedule_is_rejected():
    with pytest.raises(ConfigError):
        build_controller({"type": "schedule", "schedule": []}, KNOWN, strict=True)


def test_unknown_controller_type():
    with pytest.raises(ConfigError, match="unknown controller"):
        build_controller({"type": "quantum"}, KNOWN, strict=True)


# --- yaml/json equivalence ----------------------------------------------------


def test_compiled_json_is_preferred_and_equivalent(tmp_path):
    """bundle.py compiles YAML to JSON so the submission needs no YAML parser."""
    payload = {"type": "schedule", "schedule": [{**FULL_SEASON, "strategy": "safe_farmer"}]}
    (tmp_path / "c.yaml").write_text("type: priority\n")     # deliberately different
    (tmp_path / "c.json").write_text(json.dumps(payload))

    spec = load_spec(tmp_path / "c.yaml", strict=True)
    assert spec["type"] == "schedule", "the .json sibling wins"
    assert isinstance(build_controller(spec, KNOWN, strict=True), ScheduleController)


# --- rl stub ------------------------------------------------------------------


def test_rl_controller_explains_itself_rather_than_crashing_obscurely():
    c = build_controller({"type": "rl"}, KNOWN, strict=True)
    with pytest.raises(NotImplementedError, match="stub"):
        c.select(obs_at(0), build_all())


def test_rl_features_are_finite_and_fixed_width():
    from agentlib.controllers.rl import features

    v0, v1 = features(obs_at(0)), features(obs_at(700))
    assert len(v0) == len(v1)
    assert all(isinstance(x, float) and not math.isnan(x) for x in v0 + v1)
