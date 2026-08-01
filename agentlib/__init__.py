"""Kaggriculture agent library.

Kept as a flat top-level package (not under src/) so the submission tarball can
ship `main.py` + `agentlib/` side by side, matching the local layout exactly.
"""

from .planner import decide

__all__ = ["decide"]
