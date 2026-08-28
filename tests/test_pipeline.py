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

import compare as cmp
import evaluate as ev

from agentlib import planner
from agentlib.controllers import FixedController, build_controller
from agentlib.controllers.fixed import spec_for
from agentlib.game.observation import Obs, Tile
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


def test_margin_z_penalises_variance():
    """The ladder scores win/loss, so `Pr[win] = Phi(mu/sigma)` — dispersion is part
    of the objective, not noise. Two configs with equal mean are NOT equally good."""
    tight = {"n": 60, "mean_margin": 800.0, "stdev_margin": 30.0}
    loose = {"n": 60, "mean_margin": 800.0, "stdev_margin": 90.0}

    assert ev.score(tight, "mean_margin") == ev.score(loose, "mean_margin"), (
        "mean_margin is blind to dispersion by construction"
    )
    assert ev.score(tight, "margin_z") > ev.score(loose, "margin_z")


def test_margin_z_cannot_divide_by_zero():
    """A config that never loses would score unbounded and BO would chase it."""
    flawless = {"n": 60, "mean_margin": 500.0, "stdev_margin": 0.0}
    assert ev.score(flawless, "margin_z") == 500.0 / ev.MIN_STDEV


def test_all_shipped_protocols_load_with_disjoint_splits():
    for path in sorted((ROOT / "eval" / "protocols").glob("*.yaml")):
        proto = ev.load_protocol(path)
        train, holdout = set(proto["seeds"]["train"]), set(proto["seeds"]["holdout"])
        assert train and holdout, path.name
        assert not (train & holdout), f"{path.name}: holdout overlaps train"


def test_protocols_have_distinct_hashes():
    """compare.py keys comparability off protocol_hash — two protocols that hashed
    the same would let incomparable runs be ranked together."""
    hashes = {
        ev.load_protocol(p)["_hash"]
        for p in (ROOT / "eval" / "protocols").glob("*.yaml")
    }
    assert len(hashes) == len(list((ROOT / "eval" / "protocols").glob("*.yaml")))


# --- shed geometry -------------------------------------------------------------


def test_shed_tiles_are_the_four_centre_squares():
    """The shed is not a tile you can find by scanning `tiles`, and it is not
    'orthogonally adjacent' to anything — it is exactly these four positions.
    Getting this wrong sends a unit walking to (0,0) for 720 turns."""
    o = Obs(raw_obs())
    o.raw["farms"][0]["tiles"] = [[None] * 10 for _ in range(10)]
    assert set(o.shed_tiles) == {(4, 4), (5, 4), (4, 5), (5, 5)}
    assert o.at_shed((4, 4)) and o.at_shed((5, 5))
    assert not o.at_shed((0, 0)) and not o.at_shed((3, 4))


def test_shed_tiles_come_from_the_env_not_a_transcription():
    from kaggle_environments.envs.kaggriculture import kaggriculture as env_mod

    from agentlib.game.config import shed_access_tiles

    assert shed_access_tiles is env_mod._shed_access_tiles


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


# --- paired comparison ---------------------------------------------------------
#
# The protocol shares seeds across configs on purpose. If compare.py stops
# exploiting that, every ranking silently gets a much wider error bar than the
# design actually earns — which is how a real improvement gets discarded as noise.


def _run(margins, seat=0, split="train", env="e1", code="c1", run_id="r", opponent="pass"):
    return {
        "run_id": run_id, "split": split, "env_hash": env, "code_hash": code,
        "episodes": [
            {"seed": i, "seat": seat, "margin": m, "status": "DONE", "opponent": opponent}
            for i, m in enumerate(margins)
        ],
    }


def test_paired_cancels_common_noise():
    """A constant edge on top of a large shared seed effect must read as certain."""
    seed_effect = [0, 500, -500, 250, -250, 100]
    a = _run([s + 100 for s in seed_effect])
    b = _run(list(seed_effect))

    p = cmp.paired(a, b)
    assert p["n"] == 6
    assert p["delta"] == pytest.approx(100.0)
    assert p["sd"] == pytest.approx(0.0)          # the seed effect divides out
    assert p["rho"] == pytest.approx(1.0)
    assert p["se_unpaired"] > 100, (
        "treating these as independent must look far less certain than they are"
    )


def test_paired_keeps_genuinely_independent_noise():
    """Pairing is not a free win — uncorrelated runs get no discount."""
    a = _run([100, -100, 100, -100, 100, -100])
    b = _run([0, 0, 0, 0, 0, 0])
    p = cmp.paired(a, b)
    assert p["sd"] > 90, "no shared structure means no variance reduction"


def test_paired_reports_identical_runs():
    xs = [10, 20, 30, 40]
    p = cmp.paired(_run(xs), _run(xs))
    assert p["identical"] and p["delta"] == 0.0


def test_paired_needs_overlapping_seeds():
    """Train and holdout share no seeds; a delta across them would be nonsense."""
    a = _run([1, 2, 3])
    b = {**_run([1, 2, 3]), "episodes": [
        {"seed": 99, "seat": 0, "margin": 1, "status": "DONE"}]}
    assert cmp.paired(a, b) is None


def test_paired_ignores_errored_episodes():
    a = _run([10, 20])
    a["episodes"].append({"seed": 5, "seat": 0, "margin": None, "status": "ERROR"})
    b = _run([0, 0])
    b["episodes"].append({"seed": 5, "seat": 0, "margin": None, "status": "ERROR"})
    assert cmp.paired(a, b)["n"] == 2


def test_paired_survives_a_constant_run():
    """statistics.correlation raises on zero variance; that must not crash a report."""
    p = cmp.paired(_run([5, 5, 5, 5]), _run([1, 2, 3, 4]))
    assert p is not None and p["rho"] is None


def test_paired_distinguishes_seats_on_the_same_seed():
    """Seat matters: seed 0 seat 0 and seed 0 seat 1 are different episodes."""
    a = _run([10, 20], seat=0)
    b = _run([10, 20], seat=1)
    assert cmp.paired(a, b) is None


# --- RL feature contract -------------------------------------------------------


def test_feature_vector_matches_its_declared_names():
    """A silent shape change invalidates every trajectory already collected.

    This fails the moment someone edits `features()` without touching
    FEATURE_NAMES, which is the prompt to bump FEATURE_VERSION.
    """
    from agentlib.controllers.rl import FEATURE_NAMES, FEATURE_VERSION, features

    assert isinstance(FEATURE_VERSION, int) and FEATURE_VERSION >= 1
    vec = features(Obs(raw_obs()))
    assert len(vec) == len(FEATURE_NAMES), (
        f"features() returns {len(vec)} values but FEATURE_NAMES declares "
        f"{len(FEATURE_NAMES)}; if this is intentional, bump FEATURE_VERSION"
    )
    assert all(isinstance(v, (int, float)) for v in vec)


def test_trajectory_records_the_feature_version():
    from agentlib.controllers.rl import FEATURE_VERSION

    planner.reset()
    planner.RECORD_TRAJECTORY = True
    try:
        planner.decide(raw_obs(0))
        entry = planner.agent_for(0).journal[0]
    finally:
        planner.RECORD_TRAJECTORY = False
        planner.reset()
    assert entry["feature_version"] == FEATURE_VERSION
    assert len(entry["features"]) == len(entry["features"])
    assert len(entry["mask"]) == len(planner.build_agent().action_space)


# --- self-play seat isolation --------------------------------------------------


def test_each_seat_gets_its_own_agent():
    """Both players share one interpreter, so a module-level singleton would make
    the two seats trample each other's strategy state — including in Kaggle's own
    agent-vs-itself validation episode."""
    planner.reset()
    planner.decide(raw_obs(0))
    o = raw_obs(1)
    o["player"] = 1
    planner.decide(o)

    a, b = planner.agent_for(0), planner.agent_for(1)
    assert a is not None and b is not None
    assert a is not b, "seats must not share an Agent"
    planner.reset()
    assert planner.agent_for(0) is None and planner.agent_for(1) is None


def test_seat_config_overrides_the_process_wide_one(monkeypatch, tmp_path):
    from agentlib.settings import load_spec

    seat1 = tmp_path / "seat1.json"
    seat1.write_text('{"type": "schedule", "schedule": []}')
    monkeypatch.setenv("KAGGRICULTURE_CONFIG", str(tmp_path / "shared.json"))
    (tmp_path / "shared.json").write_text('{"type": "priority"}')
    monkeypatch.setenv("KAGGRICULTURE_CONFIG_1", str(seat1))

    assert load_spec(seat=0, strict=False)["type"] == "priority"
    assert load_spec(seat=1, strict=False)["type"] == "schedule"
    assert load_spec(strict=False)["type"] == "priority", "no seat = process-wide var"


def test_paired_keeps_opponents_apart():
    """A multi-opponent protocol plays each (seed, seat) once PER opponent.

    Keying on (seed, seat) alone made later opponents overwrite earlier ones, so a
    180-episode v3 comparison silently became a 60-episode one against whichever
    opponent was written last — with a delta to match.
    """
    def multi(vs_pass, vs_starter):
        a = _run(vs_pass, opponent="pass")
        b = _run(vs_starter, opponent="starter")
        a["episodes"] += b["episodes"]
        return a

    hi = multi([100, 100, 100], [50, 50, 50])
    lo = multi([0, 0, 0], [0, 0, 0])
    p = cmp.paired(hi, lo)
    assert p["n"] == 6, "every opponent's episodes must survive the join"
    assert p["delta"] == pytest.approx(75.0), "pooled across both opponents"


# --- controller diagnostics cross the process boundary -------------------------


def test_diagnostics_sum_scalars_and_lists_elementwise():
    eps = [
        {"controller": {"switches": 2, "fires": [1, 0, 5]}},
        {"controller": {"switches": 3, "fires": [0, 0, 7]}},
    ]
    agg = ev.aggregate_diagnostics(eps)
    assert agg["switches"] == 5
    assert agg["fires"] == [1, 0, 12], "per-rule counts must stay per-rule"
    assert agg["_episodes"] == 2


def test_diagnostics_ignore_episodes_that_reported_nothing():
    """A zero must mean 'never fired', not 'never asked'."""
    agg = ev.aggregate_diagnostics([{"controller": {}}, {}, {"controller": {"fires": [4]}}])
    assert agg["fires"] == [4] and agg["_episodes"] == 1


def test_diagnostics_skip_mismatched_list_lengths():
    """Two runs of different rule counts must not be silently zipped together."""
    agg = ev.aggregate_diagnostics([
        {"controller": {"fires": [1, 2]}},
        {"controller": {"fires": [1, 2, 3]}},
    ])
    assert agg["fires"] == [1, 2]


def test_an_unreachable_rule_shows_as_zero_fires():
    """The diagnostic that makes an inert config visible before a sweep wastes
    its budget on tuning a threshold that can never be crossed."""
    spec = {
        "type": "threshold",
        "rules": [
            {"when": {"money_gte": 10**9}, "strategy": "wheat_loop"},
            {"when": {}, "strategy": "safe_farmer"},
        ],
    }
    c = build_controller(spec, KNOWN, strict=True)
    strategies = build_all()
    for step in range(20):
        c.select(Obs(raw_obs(step)), strategies)
    fires = c.diagnostics()["fires"]
    assert fires[0] == 0, "the impossible rule must report zero"
    assert fires[1] == 20


# --- stale compiled config -----------------------------------------------------


def test_a_newer_yaml_beats_its_stale_compiled_json(tmp_path):
    """Editing a config must not be silently ignored.

    bundle.py compiles configs/*.yaml -> .json and the loader prefers the .json
    because that is all a submission ships. When the YAML is newer, preferring the
    compiled copy means the run measures the OLD config while the file on disk
    shows the new one — and configs/*.json is gitignored, so no diff reveals it.
    """
    import os

    from agentlib.settings import ConfigError, _resolve_path, load_spec

    src = tmp_path / "cfg.yaml"
    compiled = tmp_path / "cfg.json"
    compiled.write_text('{"type": "priority"}')
    src.write_text("type: threshold\nrules: [{when: {}, strategy: safe_farmer}]\n")
    os.utime(compiled, (1, 1))          # compiled is old
    os.utime(src, (2, 2))               # source is newer

    with pytest.raises(ConfigError, match="newer"):
        load_spec(src, strict=True)

    assert load_spec(src, strict=False)["type"] == "threshold", (
        "lenient mode must still run, using the newer source"
    )

    os.utime(src, (1, 1))
    os.utime(compiled, (2, 2))          # rebuilt: compiled is now current
    assert _resolve_path(src, strict=True) == compiled
    assert load_spec(src, strict=True)["type"] == "priority"


# --- WheatFarm invariants ------------------------------------------------------
#
# Not score assertions (those belong in results/), but the engine rules the
# strategy would silently violate if someone edits it.


def _farm_obs(step=0, tiles=None, hands=0, money=3000, seeds=10):
    grid = tiles or [[None for _ in range(4)] for _ in range(4)]
    farm = {"money": money, "tiles": grid, "farmer": [0, 0],
            "hands": [[1, 1]] * hands, "hires_today": 0,
            "unlocked_quadrants": ["NW", "NE"]}
    return Obs({
        "player": 0, "step": step, "day": step // 24, "hour": step % 24,
        "farms": [farm, dict(farm)], "market": {"prices": {}, "inventory": {}},
        "town": {"unlocked_shops": []},
        "private": {"shed": {}, "seeds": {"WHEAT": seeds}, "inventories": [{}]},
    })


def test_never_sows_on_the_last_turn_of_the_day():
    """Rule 3: a seed sown at hour 23 cannot be watered before the daily refresh,
    so it is a weed by morning."""
    from agentlib.game.config import TURNS_PER_DAY
    from agentlib.strategies.wheat_farm import WheatFarm

    farm = WheatFarm()
    late = farm.act(_farm_obs(step=TURNS_PER_DAY - 1))
    units = [late["farmer"], *late["hands"]]
    assert not any(u[0] == "PLANT" for u in units), "sowing this late is a guaranteed weed"

    early = farm.act(_farm_obs(step=0))
    assert any(u[0] == "PLANT" for u in [early["farmer"], *early["hands"]])


def test_never_requests_more_plants_than_seeds_held():
    """Rule 4 is atomic per crop: over-requesting drops EVERY plant that turn, so
    an off-by-one here costs the whole turn's sowing, silently."""
    from agentlib.strategies.wheat_farm import WheatFarm

    action = WheatFarm().act(_farm_obs(step=0, hands=6, seeds=2))
    units = [action["farmer"], *action["hands"]]
    assert sum(1 for u in units if u[0] == "PLANT") <= 2


def test_sowing_is_capped_by_available_labour():
    """More live plants than the units can water daily is a die-off, not growth."""
    from agentlib.strategies.wheat_farm import MAX_PLANTS_PER_UNIT, WheatFarm

    obs = _farm_obs(step=0, hands=0, seeds=99)
    jobs = WheatFarm()._classify(obs)
    assert len(jobs["PLANT"]) <= int(1 * MAX_PLANTS_PER_UNIT)


def test_holds_a_one_time_crop_to_peak_yield():
    """Harvesting at the first legal moment is 0.67 units/tile/day; holding to
    max_yield_day is 0.80."""
    from agentlib.strategies.wheat_farm import WheatFarm

    def tile(age):
        return {"kind": "PLANT", "crop": "WHEAT", "planted_day": 10 - age,
                "watered_today": True, "yield_units": 2}

    obs = _farm_obs(step=10 * 24)
    assert not WheatFarm()._ripe(obs, Tile(0, 0, tile(2))), "age 2 is legal but early"
    assert not WheatFarm()._ripe(obs, Tile(0, 0, tile(3)))
    assert WheatFarm()._ripe(obs, Tile(0, 0, tile(4))), "age 4 = max_yield_day"


def test_never_harvests_a_tile_that_would_be_a_no_op():
    """Rule 2: HARVEST below first_yield_day silently does nothing and still
    costs the unit its turn."""
    from agentlib.strategies.wheat_farm import WheatFarm

    obs = _farm_obs(step=10 * 24)
    young = Tile(0, 0, {"kind": "PLANT", "crop": "WHEAT", "planted_day": 10,
                        "watered_today": True, "yield_units": 1})
    assert not WheatFarm()._ripe(obs, young)
