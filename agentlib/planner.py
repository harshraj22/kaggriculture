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

from .actions import validate
from .controller import Controller, RuleController
from .observation import Obs
from .strategy import Strategy

#: Turns a strategy drives before the controller reconsiders. One in-game day,
#: since hired hands reset daily. A guess; tune once we can measure.
RESELECT_EVERY = 24

#: `act` failures tolerated before a strategy is dropped for the rest of the episode.
MAX_STRIKES = 3

_SAFE_ACTION = {"farmer": ["PASS"], "hands": [], "market": []}


class Agent:
    """Owns the strategy set, the controller, and the per-episode journal."""

    def __init__(self, strategies, controller: Controller, default: Strategy):
        # Appended, not prepended: the default is the last resort, so it must
        # rank below anything the controller hasn't been told about explicitly.
        if default not in strategies:
            strategies = [*strategies, default]
        self.strategies = list(strategies)
        self.controller = controller
        self.default = default
        self.reset()

    def reset(self) -> None:
        self.current: Strategy | None = None
        self.held_for = 0
        self.strikes: dict[str, int] = {}
        self.journal: list[tuple] = []
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
        due = self.current is None or self.held_for >= RESELECT_EVERY
        stale = self.current is not None and (
            self._disabled(self.current) or self.current not in self._eligible(obs)
        )
        if due or stale:
            chosen = self.controller.select(obs, self._eligible(obs)) or self.default
            if chosen is not self.current:
                self.held_for = 0
            self.current = chosen
        return self.current

    def _try_act(self, strategy: Strategy, obs: Obs) -> dict | None:
        try:
            return validate(strategy.act(obs), n_hands=len(obs.hands))
        except Exception:  # noqa: BLE001
            self._log(strategy, "act")
            self.strikes[strategy.name] = self.strikes.get(strategy.name, 0) + 1
            return None

    # --- public ---

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

        self.held_for += 1
        self.journal.append((obs.step, chosen.name, obs.money))

        for s in self.strategies:
            if not self._disabled(s):
                self._guard(s, "on_action", obs, action, chosen.name)

        return action


# --- module-level entrypoint used by main.py ---------------------------------

_AGENT: Agent | None = None


def build_agent() -> Agent:
    from .strategies import DEFAULT_ORDER, build_all, default_strategy

    return Agent(build_all(), RuleController(DEFAULT_ORDER), default_strategy())


def reset() -> None:
    """Drop cached agent state — used by the local arena between games."""
    global _AGENT
    _AGENT = None


def decide(raw_obs, config=None) -> dict:
    global _AGENT
    try:
        if _AGENT is None:
            _AGENT = build_agent()
        return _AGENT.decide(raw_obs)
    except Exception:  # noqa: BLE001 - absolute last resort
        traceback.print_exc()
        return dict(_SAFE_ACTION)


if os.environ.get("KAGGRICULTURE_STRATEGY"):
    # Pin a single strategy, for A/B runs from the arena.
    _PINNED = os.environ["KAGGRICULTURE_STRATEGY"]

    def build_agent():  # type: ignore[no-redef]
        from .strategies import build, default_strategy

        return Agent([build(_PINNED)], RuleController([_PINNED]), default_strategy())
