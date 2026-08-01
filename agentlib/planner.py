"""Top-level per-turn entry point.

Wraps strategy execution in a hard try/except: an exception inside an episode
means an errored submission and a zero, so we always fall back to a legal PASS.
"""

import os
import traceback

from .actions import Turn
from .observation import Obs
from .strategies import build

_STRATEGY = None
_ERRORS = 0
_MAX_LOGGED_ERRORS = 5


def _strategy():
    global _STRATEGY
    if _STRATEGY is None:
        _STRATEGY = build(os.environ.get("KAGGRICULTURE_STRATEGY"))
    return _STRATEGY


def reset() -> None:
    """Drop cached strategy state — used by the local arena between games."""
    global _STRATEGY, _ERRORS
    _STRATEGY = None
    _ERRORS = 0


def decide(raw_obs, config=None) -> dict:
    global _ERRORS
    try:
        obs = Obs(dict(raw_obs))
        turn = Turn(n_hands=len(obs.hands))
        return _strategy().step(obs, turn).to_dict()
    except Exception:  # noqa: BLE001 - never let the episode die
        _ERRORS += 1
        if _ERRORS <= _MAX_LOGGED_ERRORS:
            traceback.print_exc()
        return {"farmer": ["PASS"], "hands": [], "market": []}
