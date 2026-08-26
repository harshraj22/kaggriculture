"""Strategy selection.

The controller answers one question: given the eligible strategies, who drives?

`RuleController` is a priority scan. A future `PolicyController` would take the
same arguments and read a feature vector off `obs` — the eligible list is already
the action mask, which is what a learned policy needs to avoid illegal picks.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from .observation import Obs
from .strategy import Strategy


class Controller(ABC):
    @abstractmethod
    def select(self, obs: Obs, candidates: list[Strategy]) -> Strategy | None:
        """Pick a strategy from `candidates` (already filtered to eligible ones)."""


class RuleController(Controller):
    """First eligible strategy in a fixed priority order wins.

    Names not in `order` rank last, in registration order — so a newly added
    strategy is selectable without touching this class, just lower priority
    until someone ranks it.
    """

    def __init__(self, order: Sequence[str] = ()):
        self.order = list(order)

    def select(self, obs: Obs, candidates: list[Strategy]) -> Strategy | None:
        if not candidates:
            return None
        by_name = {s.name: s for s in candidates}
        for name in self.order:
            if name in by_name:
                return by_name[name]
        return candidates[0]

    def __repr__(self) -> str:
        return f"RuleController(order={self.order})"
