"""Guarantees the experiment record depends on.

If any of these break, every number in results/experiments.jsonl becomes
uninterpretable — so they matter more than they look.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentlib import planner
from agentlib.controllers import PriorityController
from agentlib.planner import Agent
from agentlib.strategies import default_strategy

sys.path.insert(0, str(ROOT / "tools"))
import evaluate as ev


def obs_at(step=0):
    farm = {
        "money": 3000, "tiles": [[None] * 5 for _ in range(5)],
        "farmer": [2, 2], "hands": [], "hires_today": 0,
        "unlocked_quadrants": ["NW"],
    }
    return {
        "player": 0, "step": step, "day": step // 24, "hour": step % 24,
        "farms": [farm, dict(farm)], "market": {"prices": {}, "inventory": {}},
        "town": {"unlocked_shops": []},
        "private": {"shed": {}, "seeds": {}, "inventories": [{}]},
    }


# --- episode isolation --------------------------------------------------------


def test_reset_clears_agent_state_between_episodes():
    """The cached agent carries strikes, journal and current selection.

    Without a reset, episode 2 inherits episode 1's history and every
    multi-episode measurement is silently contaminated.
    """
    planner.reset()
    for step in range(5):
        planner.decide(obs_at(step))
    assert len(planner.agent_for(0).journal) == 5

    planner.reset()
    assert planner.agent_for(0) is None
    planner.decide(obs_at(0))
    assert len(planner.agent_for(0).journal) == 1, "a fresh episode starts from an empty journal"


def test_strikes_do_not_leak_across_episodes():
    from agentlib.strategies.base import Strategy

    class Exploding(Strategy):
        name = "explode"

        def act(self, obs):
            raise RuntimeError("boom")

    def fresh():
        return Agent([Exploding()], PriorityController(["explode"]), default_strategy())

    a = fresh()
    for _ in range(2):
        a.decide(obs_at())
    assert a.strikes["explode"] == 2

    assert fresh().strikes == {}, "a new episode must not inherit strikes"


# --- provenance ---------------------------------------------------------------


def test_code_hash_changes_when_agentlib_changes(tmp_path, monkeypatch):
    before = ev.code_hash()
    scratch = ROOT / "agentlib" / "_hash_probe.py"
    scratch.write_text("# temporary\n")
    try:
        assert ev.code_hash() != before, "an edit under agentlib/ must change the hash"
    finally:
        scratch.unlink()
    assert ev.code_hash() == before, "and removing it must restore it"


def test_protocol_hash_is_content_addressed(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    payload = {"id": "t", "seeds": {"train": [1]}, "opponents": ["pass"]}
    a.write_text(json.dumps(payload))
    b.write_text(json.dumps(payload))
    assert ev.load_protocol(a)["_hash"] == ev.load_protocol(b)["_hash"]

    b.write_text(json.dumps({**payload, "seeds": {"train": [1, 2]}}))
    assert ev.load_protocol(a)["_hash"] != ev.load_protocol(b)["_hash"], (
        "editing a protocol must change its hash so compare.py can refuse the comparison"
    )


def test_shipped_protocol_loads_and_splits_are_disjoint():
    proto = ev.load_protocol(ROOT / "eval" / "protocols" / "v1.yaml")
    train, holdout = set(proto["seeds"]["train"]), set(proto["seeds"]["holdout"])
    assert train and holdout
    assert not (train & holdout), "holdout must never overlap the seeds we optimise on"


# --- aggregation --------------------------------------------------------------


def test_summary_ignores_errored_episodes_but_counts_them():
    episodes = [
        {"status": "DONE", "ours": 100, "theirs": 50, "margin": 50},
        {"status": "DONE", "ours": 40, "theirs": 90, "margin": -50},
        {"status": "ERROR", "ours": None, "theirs": None, "margin": None},
    ]
    s = ev.summarise(episodes)
    assert s["n"] == 2 and s["errors"] == 1
    assert s["wins"] == 1 and s["losses"] == 1
    assert s["mean_margin"] == 0.0


def test_summary_of_all_errors_does_not_crash():
    s = ev.summarise([{"status": "ERROR", "ours": None, "theirs": None, "margin": None}])
    assert s["n"] == 0 and s["errors"] == 1


@pytest.mark.parametrize("wins,n", [(0, 10), (5, 10), (10, 10)])
def test_wilson_interval_contains_the_estimate(wins, n):
    lo, hi = ev.wilson(wins, n)
    assert 0.0 <= lo <= wins / n <= hi <= 1.0
