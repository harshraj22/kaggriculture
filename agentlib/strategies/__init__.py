"""Strategy registry.

Add a strategy: define it, register it here, optionally rank it in DEFAULT_ORDER.
Nothing in the controller or the arbiter needs to change.
"""

from .base import Strategy
from .safe_farmer import SafeFarmer
from .wheat_farm import WheatFarm
from .wheat_loop import WheatLoop

REGISTRY: dict[str, type[Strategy]] = {
    SafeFarmer.name: SafeFarmer,
    WheatLoop.name: WheatLoop,
    WheatFarm.name: WheatFarm,
}

#: The fallback. Must be stateless and must not raise — see safe_farmer.py.
DEFAULT = SafeFarmer.name

#: Controller priority. Names omitted here rank last, in registration order.
DEFAULT_ORDER: tuple[str, ...] = (
    WheatFarm.name,
    WheatLoop.name,
    SafeFarmer.name,
)


def build(name: str) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; have {sorted(REGISTRY)}")
    return REGISTRY[name]()


def build_all() -> list[Strategy]:
    return [cls() for cls in REGISTRY.values()]


def default_strategy() -> Strategy:
    return build(DEFAULT)


__all__ = [
    "DEFAULT",
    "DEFAULT_ORDER",
    "REGISTRY",
    "SafeFarmer",
    "Strategy",
    "WheatFarm",
    "WheatLoop",
    "build",
    "build_all",
    "default_strategy",
]
