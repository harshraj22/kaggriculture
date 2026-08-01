"""Competition entrypoint. Must stay at the root of the submission archive.

The env calls `agent(obs)` (and may pass a second `config` argument).
Keep this file thin — all logic lives in agentlib/.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agentlib.planner import decide  # noqa: E402


def agent(obs, config=None):
    return decide(obs, config)
