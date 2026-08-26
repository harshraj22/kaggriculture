"""Fixed controller — one strategy, the whole season.

    type: fixed
    strategy: wheat_loop

This is how a single strategy gets measured in isolation, which is the unit the
whole evaluation story rests on: before asking "which schedule of strategies is
best" you need to know what each one scores on its own.

Distinct from `priority` with a one-element order: priority falls back to
`candidates[0]` when its preference is ineligible, quietly measuring something
else. This returns None instead, so the arbiter's code default takes over and
`describe()` still records what was *asked* for. A run that silently became a
different strategy is worse than one that visibly fell back.
"""

from ..game.observation import Obs
from ..settings import ConfigError
from ..strategies.base import Strategy
from .base import Controller


class FixedController(Controller):
    type = "fixed"

    def __init__(self, strategy: str):
        self.strategy = strategy

    @classmethod
    def from_spec(cls, spec: dict, known: set[str] | None = None, strict: bool = True):
        name = spec.get("strategy")
        if not isinstance(name, str) or not name:
            raise ConfigError("fixed controller needs a 'strategy' name")
        if strict and known is not None and name not in known:
            raise ConfigError(
                f"fixed controller references unknown strategy {name!r}; "
                f"registered: {sorted(known)}"
            )
        return cls(name)

    def select(self, obs: Obs, candidates: list[Strategy]) -> Strategy | None:
        return next((s for s in candidates if s.name == self.strategy), None)

    def describe(self) -> dict:
        return {"type": self.type, "strategy": self.strategy}

    def __repr__(self) -> str:
        return f"FixedController({self.strategy!r})"


def spec_for(strategy: str) -> dict:
    """Build a fixed spec in memory — no file needed.

    Used by `evaluate.py --strategy X` and by any sweep that wants a per-strategy
    baseline without writing a YAML for each one.
    """
    return {"type": FixedController.type, "strategy": strategy}
