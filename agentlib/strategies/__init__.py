"""Swappable strategies. Pick one via KAGGRICULTURE_STRATEGY env var or planner.decide()."""

from .base import Strategy
from .wheat_loop import WheatLoop

REGISTRY: dict[str, type[Strategy]] = {
    "wheat_loop": WheatLoop,
}

DEFAULT = "wheat_loop"


def build(name: str | None = None) -> Strategy:
    return REGISTRY[name or DEFAULT]()


__all__ = ["DEFAULT", "REGISTRY", "Strategy", "WheatLoop", "build"]
