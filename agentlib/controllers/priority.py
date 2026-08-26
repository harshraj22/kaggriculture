"""Priority controller — first eligible strategy in a fixed order wins.

Deterministic given eligibility, so its answer only changes when eligibility
changes. That's exactly when we want it to change, which is why it needs no
stickiness machinery of its own.
"""

from collections.abc import Sequence

from ..controller import Controller
from ..observation import Obs
from ..settings import ConfigError
from ..strategy import Strategy


class PriorityController(Controller):
    type = "priority"

    def __init__(self, order: Sequence[str] = ()):
        self.order = list(order)

    @classmethod
    def from_spec(cls, spec: dict, known: set[str] | None = None, strict: bool = True):
        from ..strategies import DEFAULT_ORDER

        order = spec.get("order", DEFAULT_ORDER)
        if not isinstance(order, (list, tuple)):
            raise ConfigError(f"'order' must be a list, got {type(order).__name__}")
        if strict and known is not None:
            unknown = [n for n in order if n not in known]
            if unknown:
                raise ConfigError(
                    f"'order' references unknown strategies {unknown}; "
                    f"registered: {sorted(known)}"
                )
        return cls(order)

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
