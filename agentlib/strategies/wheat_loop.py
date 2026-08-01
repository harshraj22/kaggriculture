"""Baseline: single-farmer wheat loop.

Deliberately simple — this is the control we measure every real strategy against,
not an attempt to win. Plant wheat, water it, harvest it, sell the shed.
"""

from ..actions import Turn, move_toward
from ..config import CROPS
from ..observation import Obs, Tile
from .base import Strategy

WHEAT = CROPS["WHEAT"]


class WheatLoop(Strategy):
    name = "wheat_loop"

    def act(self, obs: Obs, turn: Turn) -> None:
        self._market(obs, turn)
        self._farmer(obs, turn)

    # --- market ---

    def _market(self, obs: Obs, turn: Turn) -> None:
        # Keep a couple of seeds in stock.
        seeds = obs.seeds.get("WHEAT", 0)
        if seeds < 2 and obs.money >= WHEAT["seed"] * 2:
            turn.buy_seed("WHEAT", 2 - seeds)

        # Dump whatever is in the shed.
        for item, qty in obs.shed.items():
            if qty > 0 and item != "FERTILIZER":
                turn.sell(item, qty)

    # --- farmer ---

    def _farmer(self, obs: Obs, turn: Turn) -> None:
        pos = obs.farmer
        tile = obs.tile(*pos)
        if tile is None:
            return

        if tile.is_plant:
            action = self._tend(obs, tile)
            if action:
                turn.set_unit(0, action)
                return

        if tile.is_weed:
            turn.set_unit(0, ["DIG"])
            return

        if tile.empty and obs.seeds.get("WHEAT", 0) > 0:
            turn.set_unit(0, ["PLANT", "WHEAT"])
            return

        target = self._next_target(obs)
        if target:
            step = move_toward(pos, target)
            if step:
                turn.set_unit(0, step)

    def _tend(self, obs: Obs, tile: Tile) -> list | None:
        age = obs.day - tile.get("planted_day", obs.day)
        if age >= WHEAT["first_yield_day"] and tile.get("yield_units", 0) > 0:
            return ["HARVEST"]
        if not tile.get("watered_today", False):
            return ["WATER"]
        return None

    def _next_target(self, obs: Obs) -> tuple[int, int] | None:
        """Nearest tile that needs attention: thirsty plant > ripe plant > empty > weed."""
        pos = obs.farmer

        def dist(t: Tile) -> int:
            return abs(t.x - pos[0]) + abs(t.y - pos[1])

        thirsty, ripe, empty, weeds = [], [], [], []
        for t in obs.owned_tiles():
            if t.is_plant:
                if not t.get("watered_today", False):
                    thirsty.append(t)
                elif t.get("yield_units", 0) > 0:
                    ripe.append(t)
            elif t.empty:
                empty.append(t)
            elif t.is_weed:
                weeds.append(t)

        for bucket in (thirsty, ripe, empty if obs.seeds.get("WHEAT", 0) else [], weeds):
            if bucket:
                return min(bucket, key=dist).pos
        return None
