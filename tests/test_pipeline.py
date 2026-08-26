"""The three seams the project's goals hang on.

1. `FixedController` — measure one strategy in isolation.
2. `evaluate(spec=...)` / `objective()` — the Optuna interface.
3. The RL journal — features, action index, and the eligibility mask.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import evaluate as ev

from agentlib import planner
from agentlib.controllers import FixedController, build_controller
from agentlib.controllers.fixed import spec_for
from agentlib.game.observation import Obs
from agentlib.planner import Agent
from agentlib.settings import ConfigError
from agentlib.strategies import build_all, default_strategy

KNOWN = {s.name for s in build_all()}


def raw_obs(step=0, money=3000):
    farm = {"money": money, "tiles": [[None] * 5 for _ in range(5)],
            "farmer": [2, 2], "hands": [], "hires_today": 0,
            "unlocked_quadrants": ["NW"]}
    return {
        "player": 0, "step": step, "day": step // 24, "hour": step % 24,
        "farms": [farm, dict(farm)], "market": {"prices": {}, "inventory": {}},
        "town": {"unlocked_shops": []},
        "private": {"shed": {}, "seeds": {}, "inventories": [{}]},
    }


# --- 1. measuring one strategy ------------------------------------------------


def test_fixed_controller_selects_only_its_strategy():
    c = build_controller(spec_for("wheat_loop"), KNOWN, strict=True)
    assert isinstance(c, FixedController)
    for step in (0, 300, 719):
        assert c.select(Obs(raw_obs(step)), build_all()).name == "wheat_loop"


def test_fixed_defers_rather_than_silently_measuring_something_else():
    """The difference from `priority` with a one-element order: priority falls
    back to candidates[0], which would quietly measure a different strategy."""
    c = build_controller(spec_for("wheat_loop"), KNOWN, strict=True)
    only_safe = [s for s in build_all() if s.name == "safe_farmer"]
    assert c.select(Obs(raw_obs()), only_safe) is None

    from agentlib.controllers import PriorityController

    assert PriorityController(["wheat_loop"]).select(Obs(raw_obs()), only_safe).name == "safe_farmer"


def test_fixed_rejects_an_unknown_strategy():
    with pytest.raises(ConfigError, match="unknown strategy"):
        build_controller(spec_for("does_not_exist"), KNOWN, strict=True)


def test_every_registered_strategy_is_measurable_in_isolation():
    """The whole per-strategy evaluation story rests on this holding for all of them."""
    for name in KNOWN:
        c = build_controller(spec_for(name), KNOWN, strict=True)
        assert c.describe() == {"type": "fixed", "strategy": name}


# --- 2. the Optuna seam --------------------------------------------------------


def test_evaluate_accepts_an_in_memory_spec(tmp_path, monkeypatch):
    """An Optuna trial proposes a config that never exists on disk."""
    proto = tmp_path / "p.json"
    proto.write_text('{"id": "test", "episode_steps": 48, "swap_seats": false, '
                     '"opponents": ["pass"], "seeds": {"train": [7]}}')
    monkeypatch.setattr(ev, "RESULTS", tmp_path / "out.jsonl")

    rec = ev.evaluate(spec=spec_for("safe_farmer"), protocol_path=proto, jobs=1)
    assert rec["config_path"] is None, "no file was involved"
    assert rec["config_hash"], "identity comes from content instead"
    assert rec["controller"] == {"type": "fixed", "strategy": "safe_farmer"}
    assert rec["summary"]["n"] == 1


def test_objective_returns_a_scalar(tmp_path, monkeypatch):
    proto = tmp_path / "p.json"
    proto.write_text('{"id": "test", "episode_steps": 48, "swap_seats": false, '
                     '"opponents": ["pass"], "seeds": {"train": [7]}}')
    monkeypatch.setattr(ev, "RESULTS", tmp_path / "out.jsonl")

    value = ev.objective(spec_for("safe_farmer"), protocol_path=proto, jobs=1,
                         study="s", trial=3)
    assert isinstance(value, float)

    import json
    rec = json.loads((tmp_path / "out.jsonl").read_text().splitlines()[-1])
    assert rec["study"] == "s" and rec["trial"] == 3, "joinable with Optuna's own storage"


def test_objective_survives_a_config_that_errors_every_episode(tmp_path, monkeypatch):
    """One bad proposal must not kill a 200-trial study."""
    monkeypatch.setattr(ev, "evaluate", lambda **kw: {"summary": {"n": 0, "errors": 4}})
    assert ev.objective({"type": "priority"}) == float("-inf")


# --- 3. the RL seam ------------------------------------------------------------


def test_action_space_is_stable_and_deduplicated():
    """Indices must mean the same thing across runs, and `build_all()` +
    `default_strategy()` construct separate objects — so dedupe must be by name."""
    agent = Agent(build_all(), build_controller({"type": "priority"}, KNOWN), default_strategy())
    assert agent.action_space == sorted(KNOWN)
    assert len(agent.action_space) == len(set(agent.action_space))
    assert len(agent.strategies) == len(KNOWN)
    assert agent.default is next(s for s in agent.strategies if s.name == agent.default.name)


def test_journal_is_cheap_by_default():
    agent = Agent(build_all(), build_controller({"type": "priority"}, KNOWN), default_strategy())
    agent.decide(raw_obs())
    assert isinstance(agent.journal[0], tuple), "submissions pay nothing for training plumbing"


def test_trajectory_records_what_a_trainer_needs(monkeypatch):
    monkeypatch.setattr(planner, "RECORD_TRAJECTORY", True)
    agent = Agent(build_all(), build_controller({"type": "priority"}, KNOWN), default_strategy())
    agent.decide(raw_obs(step=5))

    t = agent.journal[0]
    assert set(t) >= {"step", "features", "action", "mask", "money"}
    assert 0 <= t["action"] < len(agent.action_space)
    assert len(t["mask"]) == len(agent.action_space)
    assert t["mask"][t["action"]], "the chosen action must have been legal"
    assert all(isinstance(f, float) for f in t["features"])


def test_mask_reflects_eligibility_and_cannot_be_rebuilt_afterwards(monkeypatch):
    """The mask is the one part of a transition that is unrecoverable later —
    without it you can't know which actions were legal, so you can't compute
    correct log-probabilities offline."""
    monkeypatch.setattr(planner, "RECORD_TRAJECTORY", True)

    strategies = build_all()
    for s in strategies:
        if s.name == "wheat_loop":
            s.is_eligible = lambda obs: False

    agent = Agent(strategies, build_controller({"type": "priority"}, KNOWN), default_strategy())
    agent.decide(raw_obs())

    t = agent.journal[0]
    idx = agent.action_space.index("wheat_loop")
    assert t["mask"][idx] is False
    assert t["mask"][agent.action_space.index("safe_farmer")] is True


# --- the submission boundary ---------------------------------------------------


def test_agentlib_never_loads_a_dotenv():
    """`.env` is a tools-only convenience.

    The agent runs in Kaggle's sandbox: no .env file, no python-dotenv. If
    `agentlib` read one, local runs and submissions would resolve config
    differently — the same class of bug as `__file__` (fine locally, errored
    every submission) and `ACTIVE_CONFIG.exists()` (fine locally, silently fell
    back to the builtin inside the tarball).
    """
    offenders = [
        p.relative_to(ROOT)
        for p in (ROOT / "agentlib").rglob("*.py")
        if "dotenv" in p.read_text()
    ]
    assert not offenders, f"agentlib must not touch dotenv: {offenders}"


def test_agentlib_has_no_unguarded_third_party_imports():
    """The agent may import stdlib and `kaggle_environments`, nothing else.

    Anything else risks an Error submission. A third-party import is acceptable
    only inside a `try/except ImportError` that degrades gracefully — which is how
    `settings.py` handles PyYAML, since only compiled `.json` configs ship.
    """
    import ast

    allowed = {"kaggle_environments", "agentlib"}

    def walk(node, guarded, out, path):
        for child in ast.iter_child_nodes(node):
            child_guarded = guarded
            if isinstance(child, ast.Try):
                catches_import = any(
                    h.type is not None
                    and "ImportError" in ast.dump(h.type)
                    for h in child.handlers
                )
                for stmt in child.body:
                    walk(stmt, guarded or catches_import, out, path)
                for other in child.handlers + child.orelse + child.finalbody:
                    walk(other, guarded, out, path)
                continue

            names = []
            if isinstance(child, ast.Import):
                names = [a.name.split(".")[0] for a in child.names]
            elif isinstance(child, ast.ImportFrom) and child.level == 0:
                names = [(child.module or "").split(".")[0]]

            for n in names:
                if n and n not in allowed and n not in sys.stdlib_module_names and not guarded:
                    out.append(f"{path}: {n} (line {child.lineno})")

            walk(child, child_guarded, out, path)

    bad: list[str] = []
    for p in (ROOT / "agentlib").rglob("*.py"):
        walk(ast.parse(p.read_text()), False, bad, p.relative_to(ROOT))

    assert not bad, (
        "unguarded third-party imports in agentlib — these will fail in the "
        f"submission sandbox: {bad}"
    )


def test_broken_recording_does_not_cost_the_episode(monkeypatch):
    monkeypatch.setattr(planner, "RECORD_TRAJECTORY", True)
    agent = Agent(build_all(), build_controller({"type": "priority"}, KNOWN), default_strategy())
    monkeypatch.setattr(agent, "_record", lambda *a: 1 / 0)

    action = agent.decide(raw_obs())
    assert action["farmer"], "recording is diagnostics; it must not break play"
    assert agent.journal, "and it degrades to the cheap journal entry"
