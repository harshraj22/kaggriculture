"""Per-turn arbiter: observe everyone, pick one, act, tell everyone.

Failure policy, in order of last resort:

1. A strategy's `act` raises or returns something malformed
   -> fall back to the default strategy for this turn, force re-selection next
      turn, and record a strike. Three strikes disables it for the episode.
2. The default strategy itself fails
   -> return a literal PASS.

The default is never disabled and never ineligible. `observe`/`on_action`
failures are contained per strategy and do not change who acts, since the
strategy that raised was not necessarily the one driving.
"""

import os
import traceback

from .controllers.base import Controller
from .game.actions import validate
from .game.observation import Obs
from .strategies.base import Strategy

#: `act` failures tolerated before a strategy is dropped for the rest of the episode.
MAX_STRIKES = 3

#: When on, the journal records what an RL trainer needs per step: the feature
#: vector, the chosen action INDEX, and the eligibility MASK. The mask is the part
#: that cannot be reconstructed afterwards — without it you can't tell which
#: actions were legal at that step, so you can't compute correct log-probabilities
#: offline. Off by default: a submission should pay nothing for training plumbing.
RECORD_TRAJECTORY = bool(os.environ.get("KAGGRICULTURE_RECORD_TRAJECTORY"))

_SAFE_ACTION = {"farmer": ["PASS"], "hands": [], "market": []}


class Agent:
    """Owns the strategy set, the controller, and the per-episode journal."""

    def __init__(self, strategies, controller: Controller, default: Strategy):
        # Deduplicate BY NAME, not by identity. `build_all()` and
        # `default_strategy()` each construct fresh objects, so an identity check
        # would append a second SafeFarmer: observe/on_action would fire twice on
        # two instances with divergent state, and the RL action space would carry
        # a duplicate entry whose index is unreachable.
        by_name: dict[str, Strategy] = {}
        for s in strategies:
            by_name.setdefault(s.name, s)
        # Appended, not prepended: the default is the last resort, so it must
        # rank below anything the controller hasn't been told about explicitly.
        if default.name in by_name:
            default = by_name[default.name]
        else:
            by_name[default.name] = default

        self.strategies = list(by_name.values())
        self.controller = controller
        self.default = default
        #: Fixed ordering of strategy names — the RL action space. Sorted rather
        #: than registration-ordered so an index means the same thing across runs
        #: and machines; a policy trained yesterday stays valid today. Adding a
        #: strategy DOES shift indices, which is correct: the action space changed
        #: and an old policy is genuinely no longer applicable.
        self.action_space = sorted(s.name for s in self.strategies)
        self.reset()

    def reset(self) -> None:
        self.current: Strategy | None = None
        self.strikes: dict[str, int] = {}
        self.journal: list[tuple] = []
        self._controller_strikes = 0
        # Controllers may hold state; without this, reusing an Agent across
        # episodes leaks it and every measurement after the first is contaminated.
        try:
            self.controller.reset()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        for s in self.strategies:
            self._guard(s, "on_episode_start")

    # --- internals ---

    def _guard(self, strategy: Strategy, method: str, *args) -> bool:
        """Call a non-acting hook. Returns False if it raised."""
        try:
            getattr(strategy, method)(*args)
            return True
        except Exception:  # noqa: BLE001 - a broken hook must not end the episode
            self._log(strategy, method)
            return False

    def _log(self, strategy: Strategy, where: str) -> None:
        n = self.strikes.get(strategy.name, 0)
        if n < MAX_STRIKES:
            print(f"[agentlib] {strategy.name}.{where} raised:")
            traceback.print_exc()

    def _disabled(self, strategy: Strategy) -> bool:
        if strategy is self.default:
            return False
        return self.strikes.get(strategy.name, 0) >= MAX_STRIKES

    def _eligible(self, obs: Obs) -> list[Strategy]:
        out = []
        for s in self.strategies:
            if self._disabled(s):
                continue
            try:
                if s.is_eligible(obs):
                    out.append(s)
            except Exception:  # noqa: BLE001 - treat a broken check as "not eligible"
                self._log(s, "is_eligible")
        return out or [self.default]

    def _pick(self, obs: Obs) -> Strategy:
        """Consulted every turn.

        Stickiness, if any, belongs to the controller — only it knows whether its
        answer can change between turns. A schedule controller, for instance, is a
        pure function of turn number, and holding its answer for N turns would
        push its boundaries off by up to N.
        """
        try:
            chosen = self.controller.select(obs, self._eligible(obs))
        except Exception:  # noqa: BLE001 - a broken controller must not end the episode
            self._log_controller()
            chosen = None
        self.current = chosen or self.default
        return self.current

    def _log_controller(self) -> None:
        self._controller_strikes = getattr(self, "_controller_strikes", 0) + 1
        if self._controller_strikes <= MAX_STRIKES:
            print(f"[agentlib] {type(self.controller).__name__}.select raised:")
            traceback.print_exc()

    def _try_act(self, strategy: Strategy, obs: Obs) -> dict | None:
        try:
            return validate(strategy.act(obs), n_hands=len(obs.hands))
        except Exception:  # noqa: BLE001
            self._log(strategy, "act")
            self.strikes[strategy.name] = self.strikes.get(strategy.name, 0) + 1
            return None

    # --- public ---

    def _guard_record(self, obs: Obs, chosen: Strategy) -> None:
        """Recording is diagnostics; a failure here must not cost the episode."""
        try:
            self._record(obs, chosen)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self.journal.append((obs.step, chosen.name, obs.money))

    def _record(self, obs: Obs, chosen: Strategy) -> None:
        """One RL transition. `action_space` is the fixed strategy ordering, so an
        index means the same thing across episodes; `mask` says which were legal."""
        from .controllers.rl import FEATURE_VERSION, features

        eligible = {s.name for s in self._eligible(obs)}
        self.journal.append({
            "step": obs.step,
            "money": obs.money,
            "opponent_money": obs.opponent_money,
            "feature_version": FEATURE_VERSION,
            "features": features(obs),
            "action": self.action_space.index(chosen.name),
            "mask": [name in eligible for name in self.action_space],
        })

    def decide(self, raw_obs) -> dict:
        obs = Obs(dict(raw_obs))

        for s in self.strategies:
            if not self._disabled(s):
                self._guard(s, "observe", obs)

        chosen = self._pick(obs)
        action = self._try_act(chosen, obs)

        if action is None:
            # Selected strategy failed: fall back, and re-pick next turn.
            self.current = None
            if chosen is not self.default:
                chosen = self.default
                action = self._try_act(self.default, obs)
            if action is None:
                action = dict(_SAFE_ACTION)
                chosen = self.default

        if RECORD_TRAJECTORY:
            self._guard_record(obs, chosen)
        else:
            self.journal.append((obs.step, chosen.name, obs.money))

        for s in self.strategies:
            if not self._disabled(s):
                self._guard(s, "on_action", obs, action, chosen.name)

        return action


# --- module-level entrypoint used by main.py ---------------------------------

#: One Agent **per seat**, not one per process.
#:
#: `kaggle_environments` runs both players inside a single interpreter and
#: `agentlib` is cached in `sys.modules`, so a module-level singleton is shared by
#: both seats. That silently breaks any episode where we play both sides — mirror
#: matches, self-play, and **Kaggle's own submission validation episode**, which is
#: agent-vs-a-copy-of-itself. One Agent receiving interleaved observations from two
#: different farms corrupts every stateful strategy (`WheatLoop.claims`), the
#: strike counters, and the RL journal.
_AGENTS: dict[int, Agent] = {}


def agent_for(seat: int = 0) -> Agent | None:
    """The Agent driving `seat` this episode, if one has been built."""
    return _AGENTS.get(int(seat))


def build_agent(config_path=None, strict: bool = False, seat: int | None = None) -> Agent:
    """Assemble the agent from a controller config.

    `strict=False` by default because this runs inside episodes, where a bad
    config must degrade play rather than error the submission. Tools and tests
    pass `strict=True` so a typo fails loudly instead of silently changing what
    was measured.
    """
    from .controllers import build_controller
    from .settings import load_spec
    from .strategies import build_all, default_strategy

    strategies = build_all()
    spec = load_spec(config_path, strict=strict, seat=seat)
    controller = build_controller(spec, known={s.name for s in strategies}, strict=strict)
    agent = Agent(strategies, controller, default_strategy())
    agent.spec = spec
    return agent


def reset() -> None:
    """Drop cached agent state.

    MUST be called between episodes. The cached agent carries strikes, journal
    and current selection, so without this, episode 2 inherits episode 1's
    history and every multi-episode measurement is silently contaminated.
    """
    _AGENTS.clear()


def decide(raw_obs, config=None) -> dict:
    try:
        # Seat comes from the observation, which is the only place it exists: the
        # loader gives an agent no argument telling it which player it is.
        seat = 0
        if isinstance(raw_obs, dict):
            try:
                seat = int(raw_obs.get("player", 0) or 0)
            except (TypeError, ValueError):
                seat = 0
        agent = _AGENTS.get(seat)
        if agent is None:
            agent = _AGENTS[seat] = build_agent(seat=seat)
        return agent.decide(raw_obs)
    except Exception:  # noqa: BLE001 - absolute last resort
        traceback.print_exc()
        return dict(_SAFE_ACTION)
