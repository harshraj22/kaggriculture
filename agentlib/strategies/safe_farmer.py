"""SafeFarmer — the default strategy and the universal fallback.

Two hard constraints make this different from an ordinary strategy:

1. **Stateless.** It may be invoked at any turn, including immediately after
   another strategy has been driving for 300 turns. It must never assume it saw
   the previous turn.
2. **Must not raise.** It is what we fall back *to*. Every lookup is defensive;
   there is no arithmetic that can divide by zero and no indexing that can go
   out of range.

Within those limits it plays a reasonable wheat loop: keep plants alive, harvest
what's ripe, clear weeds, plant into empty tiles, and sell the shed.
"""

from ..game.actions import TurnPlan, move_toward
from ..game.config import CROPS
from ..game.observation import Obs
from .base import Strategy

CROP = "WHEAT"


class SafeFarmer(Strategy):
    name = "safe_farmer"

    def is_eligible(self, obs: Obs) -> bool:
        # The fallback is always available, by construction.
        return True

    def act(self, obs: Obs) -> dict:
        plan = TurnPlan(n_hands=len(obs.hands))
        self._market(obs, plan)
        for idx in range(1 + len(obs.hands)):
            plan.set_unit(idx, self._unit_action(obs, idx))
        return plan.to_dict()

    # --- market ---

    def _market(self, obs: Obs, plan: TurnPlan) -> None:
        seed_cost = CROPS.get(CROP, {}).get("seed", 0)
        held = obs.seeds.get(CROP, 0)
        # Keep a small buffer. PLANT validation is atomic per crop: if units
        # collectively request more than we hold, every one of them is dropped.
        want = max(0, 2 - held)
        if want and seed_cost and obs.money >= seed_cost * want:
            plan.buy_seed(CROP, want)

        for item, qty in obs.shed.items():
            if isinstance(qty, int) and qty > 0 and item != "FERTILIZER":
                plan.sell(item, qty)

    # --- units ---

    def _unit_action(self, obs: Obs, idx: int) -> list:
        pos = obs.farmer if idx == 0 else obs.hands[idx - 1]
        tile = obs.tile(*pos)
        if tile is None:
            return ["PASS"]

        if tile.is_plant:
            if not tile.get("watered_today", False):
                return ["WATER"]
            if tile.get("yield_units", 0) > 0:
                return ["HARVEST"]
        elif tile.is_weed:
            return ["DIG"]
        elif tile.empty and obs.seeds.get(CROP, 0) > 0:
            # Only unit 0 plants, so concurrent PLANT requests can't exceed a
            # 2-seed buffer and trip the atomic-validation rule.
            if idx == 0:
                return ["PLANT", CROP]

        target = self._target(obs, pos, idx)
        if target:
            step = move_toward(pos, target)
            if step:
                return step
        return ["PASS"]

    def _target(self, obs: Obs, pos, idx: int):
        """Nearest tile needing attention: thirsty > ripe > empty > weed."""
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

        plantable = empty if (idx == 0 and obs.seeds.get(CROP, 0) > 0) else []
        for bucket in (thirsty, ripe, plantable, weeds):
            if bucket:
                return min(bucket, key=lambda t: abs(t.x - pos[0]) + abs(t.y - pos[1])).pos
        return None
