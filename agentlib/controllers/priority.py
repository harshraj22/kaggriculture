"""Priority controller — first eligible strategy in a fixed order wins.

Deterministic given eligibility, so its answer only changes when eligibility
changes. That's exactly when we want it to change, which is why it needs no
stickiness machinery of its own.
"""

from collections.abc import Sequence

from ..controller import Controller
from ..observation import Obs
from ..strategy import Strategy


class PriorityController(Controller):
    type = "priority"

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

    def describe(self) -> dict:
        return {"type": self.type, "order": self.order}

    def __repr__(self) -> str:
        return f"PriorityController(order={self.order})"
