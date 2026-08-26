"""WheatLoop — the stateful reference implementation.

Exists as much to demonstrate the stateful pattern as to play well: it keeps
cross-turn state (shed location, tile assignments, harvest tally), updates that
state in `observe` whether or not it is driving, and reconciles in `on_action`
when some other strategy took the turn.

Compare with `SafeFarmer`, which is stateless by requirement.
"""

from ..actions import TurnPlan, move_toward
from ..config import CROPS
from ..desk import MarketDesk
from ..observation import Obs
from ..strategy import Strategy

CROP = "WHEAT"
WHEAT = CROPS[CROP]

#: Hands to hire per day. fib() pricing makes the first several nearly free.
TARGET_HANDS = 6


class WheatLoop(Strategy):
    name = "wheat_loop"

    def __init__(self) -> None:
        self.desk = MarketDesk()
        self.on_episode_start()

    def on_episode_start(self) -> None:
        self.shed_pos: tuple[int, int] | None = None
        self.last_day: int = -1
        self.harvested: int = 0
        #: unit index -> tile it is currently walking to. Cleared when we lose the turn.
        self.claims: dict[int, tuple[int, int]] = {}

    # --- state, updated every turn regardless of who drives ---

    def observe(self, obs: Obs) -> None:
        # Units respawn at the shed at hour 0, which is the one moment its
        # location is unambiguous.
        if obs.hour == 0:
            self.shed_pos = obs.farmer
            self.claims.clear()
        if obs.day != self.last_day:
            self.last_day = obs.day
        # Claims on tiles that are no longer worth visiting are dead weight.
        for idx, pos in list(self.claims.items()):
            tile = obs.tile(*pos)
            if tile is None or not (tile.is_plant or tile.empty or tile.is_weed):
                self.claims.pop(idx, None)

    def on_action(self, obs: Obs, action: dict, chosen: str) -> None:
        if chosen != self.name:
            # Someone else moved our units; every route we planned is stale.
            self.claims.clear()
            return
        if action.get("farmer", [None])[0] == "HARVEST":
            self.harvested += 1

    # --- acting ---

    def act(self, obs: Obs) -> dict:
        plan = TurnPlan(n_hands=len(obs.hands))

        self.desk.hire(obs, plan, TARGET_HANDS)
        self.desk.ensure_seeds(obs, plan, CROP, want=2 + len(obs.hands))
        self.desk.liquidate(obs, plan)

        taken: set[tuple[int, int]] = set()
        for idx in range(1 + len(obs.hands)):
            plan.set_unit(idx, self._unit_action(obs, idx, taken))
        return plan.to_dict()

    def _unit_action(self, obs: Obs, idx: int, taken: set) -> list:
        pos = obs.farmer if idx == 0 else obs.hands[idx - 1]
        tile = obs.tile(*pos)
        if tile is None:
            return ["PASS"]

        if tile.is_plant:
            if not tile.get("watered_today", False):
                self.claims.pop(idx, None)
                return ["WATER"]
            if tile.get("yield_units", 0) > 0:
                self.claims.pop(idx, None)
                return ["HARVEST"]
        elif tile.is_weed:
            self.claims.pop(idx, None)
            return ["DIG"]

        target = self.claims.get(idx) or self._claim(obs, pos, taken)
        if target is None:
            return ["PASS"]
        taken.add(target)

        if target == pos:
            self.claims.pop(idx, None)
            # Only unit 0 plants: PLANT validation is atomic per crop, so if the
            # units collectively out-request our seed count, ALL of them are
            # dropped. Serialising planting keeps that from ever happening.
            if idx == 0 and obs.seeds.get(CROP, 0) > 0:
                return ["PLANT", CROP]
            return ["PASS"]

        self.claims[idx] = target
        return move_toward(pos, target) or ["PASS"]

    def _claim(self, obs: Obs, pos, taken: set):
        """Nearest unclaimed tile needing work: thirsty > ripe > empty > weed."""
        claimed = taken | set(self.claims.values())
        thirsty, ripe, empty, weeds = [], [], [], []
        for t in obs.owned_tiles():
            if t.pos in claimed:
                continue
            if t.is_plant:
                if not t.get("watered_today", False):
                    thirsty.append(t)
                elif t.get("yield_units", 0) > 0:
                    ripe.append(t)
            elif t.empty:
                empty.append(t)
            elif t.is_weed:
                weeds.append(t)

        plantable = empty if obs.seeds.get(CROP, 0) > 0 else []
        for bucket in (thirsty, ripe, plantable, weeds):
            if bucket:
                return min(bucket, key=lambda t: abs(t.x - pos[0]) + abs(t.y - pos[1])).pos
        return None
