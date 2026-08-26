"""Controller registry.

Add a controller: define it, register it here. Nothing else changes.
"""

import traceback

from ..settings import ConfigError
from .priority import PriorityController
from .rl import PolicyController
from .schedule import ScheduleController

REGISTRY = {
    PriorityController.type: PriorityController,
    ScheduleController.type: ScheduleController,
    PolicyController.type: PolicyController,
}


def _build(spec: dict, known: set[str] | None, strict: bool):
    kind = spec.get("type")
    if kind not in REGISTRY:
        raise ConfigError(f"unknown controller type {kind!r}; have {sorted(REGISTRY)}")

    if kind == ScheduleController.type:
        return ScheduleController.from_spec(spec, known=known, strict=strict)
    if kind == PolicyController.type:
        return PolicyController(spec.get("policy"))

    from ..strategies import DEFAULT_ORDER

    return PriorityController(spec.get("order", DEFAULT_ORDER))


def build_controller(spec: dict, known: set[str] | None = None, strict: bool = True):
    """Build a controller from a resolved spec.

    In lenient mode any failure degrades to the priority controller rather than
    ending the episode — a bad config should cost play quality, never a zero.
    """
    try:
        return _build(spec, known, strict)
    except Exception:
        if strict:
            raise
        traceback.print_exc()
        print("[agentlib] bad controller spec; falling back to priority controller")
        from ..strategies import DEFAULT_ORDER

        return PriorityController(DEFAULT_ORDER)


__all__ = [
    "REGISTRY",
    "PolicyController",
    "PriorityController",
    "ScheduleController",
    "build_controller",
]
