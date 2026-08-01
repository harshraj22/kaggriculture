"""Strategy interface.

A Strategy is stateful across turns within an episode (the same object is reused),
so it can remember the shed location, planting plans, sale schedules, etc.
"""

from ..actions import Turn
from ..observation import Obs


class Strategy:
    name = "base"

    def __init__(self) -> None:
        self.shed_pos: tuple[int, int] | None = None
        self.last_day: int = -1

    # --- lifecycle hooks ---

    def on_new_day(self, obs: Obs, turn: Turn) -> None:
        """Called once at the first turn we see of each day."""

    def act(self, obs: Obs, turn: Turn) -> None:
        """Fill in turn.farmer / turn.hands / turn.market."""
        raise NotImplementedError

    # --- shared helpers ---

    def observe(self, obs: Obs) -> Obs:
        """Track cross-turn facts. Units spawn at the shed at hour 0 of each day."""
        if obs.hour == 0 and self.shed_pos is None:
            self.shed_pos = obs.farmer
        obs._shed_pos = self.shed_pos
        return obs

    def step(self, obs: Obs, turn: Turn) -> Turn:
        self.observe(obs)
        if obs.day != self.last_day:
            self.last_day = obs.day
            self.on_new_day(obs, turn)
        self.act(obs, turn)
        return turn
