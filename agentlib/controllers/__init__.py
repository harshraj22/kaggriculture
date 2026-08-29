"""Controller registry.

Adding a controller is two steps: write the class, add it here. Dispatch is
uniform — `Controller.from_spec` is part of the interface, so this module has no
per-controller special cases and never needs editing beyond the REGISTRY entry.
"""

import traceback

from ..settings import ConfigError
from .allocate import AllocateController
from .base import Controller
from .fixed import FixedController
from .priority import PriorityController
from .rl import PolicyController
from .schedule import ScheduleController
from .threshold import ThresholdController

REGISTRY: dict[str, type[Controller]] = {
    PriorityController.type: PriorityController,
    ScheduleController.type: ScheduleController,
    PolicyController.type: PolicyController,
    ThresholdController.type: ThresholdController,
    FixedController.type: FixedController,
    AllocateController.type: AllocateController,
}


def register(cls: type[Controller]) -> type[Controller]:
    """Optional decorator, for controllers defined outside this package."""
    REGISTRY[cls.type] = cls
    return cls


def build_controller(spec: dict, known: set[str] | None = None, strict: bool = True) -> Controller:
    """Build a controller from a resolved spec.

    In lenient mode any failure degrades to the priority controller rather than
    ending the episode — a bad config should cost play quality, never a zero.
    """
    try:
        kind = spec.get("type")
        if kind not in REGISTRY:
            raise ConfigError(f"unknown controller type {kind!r}; have {sorted(REGISTRY)}")
        return REGISTRY[kind].from_spec(spec, known=known, strict=strict)
    except Exception:
        if strict:
            raise
        traceback.print_exc()
        print("[agentlib] bad controller spec; falling back to priority controller")
        from ..strategies import DEFAULT_ORDER

        return PriorityController(DEFAULT_ORDER)


__all__ = [
    "REGISTRY",
    "AllocateController",
    "FixedController",
    "PolicyController",
    "PriorityController",
    "ScheduleController",
    "ThresholdController",
    "build_controller",
    "register",
]
