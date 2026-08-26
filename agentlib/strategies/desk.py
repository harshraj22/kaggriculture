"""MarketDesk — shared market logic, composed into strategies.

This is the answer to "every strategy needs to sell things": a plain object a
strategy holds and delegates to, not a second strategy competing for the turn.

`SafeFarmer` deliberately does NOT use this. It's the fallback, so it stays
self-contained — one less shared object whose failure could take the fallback
down with it.
"""

from ..game.actions import TurnPlan
from ..game.config import ANIMALS, CROPS, cumulative_hire_cost
from ..game.market import dump_capacity
from ..game.observation import Obs


class MarketDesk:
    """Stateless helper. Fills market orders on a plan."""

    def __init__(self, throttle: bool = True, floor_ratio: float = 0.5):
        #: Cap each sale so we don't walk our own price down the curve.
        self.throttle = throttle
        self.floor_ratio = floor_ratio

    # --- procurement ---

    def ensure_seeds(self, obs: Obs, plan: TurnPlan, crop: str, want: int) -> None:
        held = obs.seeds.get(crop, 0)
        need = max(0, want - held)
        cost = CROPS.get(crop, {}).get("seed", 0)
        if need and cost and obs.money >= cost * need:
            plan.buy_seed(crop, need)

    def ensure_feed(self, obs: Obs, plan: TurnPlan, want: int) -> None:
        """Animals eat wheat daily; running out loses the animal outright."""
        held = obs.shed.get("WHEAT", 0)
        need = max(0, want - held)
        price = obs.prices.get("WHEAT", 0)
        if need and price and obs.money >= price * need:
            plan.buy_product("WHEAT", need)

    def buy_animal(self, obs: Obs, plan: TurnPlan, animal: str) -> None:
        cost = ANIMALS.get(animal, {}).get("cost", 0)
        if cost and obs.money >= cost:
            plan.buy_animal(animal, 1)

    # --- labour & land ---

    def hire(self, obs: Obs, plan: TurnPlan, target_hands: int) -> None:
        """Hire up to `target_hands` for today, if affordable.

        Cost is fib(n) per hire and resets daily, so the first several hands are
        close to free relative to a single harvest.
        """
        already = obs.hires_today
        need = max(0, target_hands - already)
        if need and obs.money >= cumulative_hire_cost(already + need) :
            plan.hire(need)

    # --- disposal ---

    def liquidate(self, obs: Obs, plan: TurnPlan, keep: dict | None = None) -> None:
        """Sell the shed, optionally holding back reserves (e.g. wheat for feed)."""
        keep = keep or {}
        inv = obs.market_inventory
        for item, qty in obs.shed.items():
            if not isinstance(qty, int) or qty <= 0:
                continue
            qty -= keep.get(item, 0)
            if qty <= 0:
                continue
            if self.throttle and item in inv:
                qty = min(qty, dump_capacity(item, inv[item], self.floor_ratio))
            plan.sell(item, qty)
