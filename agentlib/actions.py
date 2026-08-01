"""Action construction helpers.

The env expects:
    {"farmer": [ACTION, *args], "hands": [[ACTION, *args], ...], "market": [[ORDER, ITEM, N], ...]}
"""

from .config import MAX_MARKET_ORDERS_PER_TURN, MOVE_DELTA

PASS = ["PASS"]


def move_toward(src: tuple[int, int], dst: tuple[int, int]) -> list[str] | None:
    """One step of greedy pathing. Returns None if already there."""
    sx, sy = src
    dx, dy = dst
    if (sx, sy) == (dx, dy):
        return None
    # Move on the longer axis first to keep paths L-shaped and predictable.
    if abs(dx - sx) >= abs(dy - sy) and dx != sx:
        return ["EAST"] if dx > sx else ["WEST"]
    if dy != sy:
        return ["SOUTH"] if dy > sy else ["NORTH"]
    return ["EAST"] if dx > sx else ["WEST"]


def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def adjacent(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return manhattan(a, b) == 1


def neighbors(pos: tuple[int, int]) -> list[tuple[int, int]]:
    x, y = pos
    return [(x + dx, y + dy) for dx, dy in MOVE_DELTA.values()]


class Turn:
    """Accumulator for one turn's response."""

    def __init__(self, n_hands: int = 0):
        self.farmer: list = list(PASS)
        self.hands: list[list] = [list(PASS) for _ in range(n_hands)]
        self.market: list[list] = []

    def set_unit(self, idx: int, action: list) -> None:
        """idx 0 = main farmer, 1..n = hired hands."""
        if idx == 0:
            self.farmer = action
        elif 1 <= idx <= len(self.hands):
            self.hands[idx - 1] = action

    def order(self, *parts) -> bool:
        """Queue a market order. Returns False if the per-turn cap is already hit."""
        if len(self.market) >= MAX_MARKET_ORDERS_PER_TURN:
            return False
        self.market.append(list(parts))
        return True

    def buy_seed(self, crop: str, n: int = 1) -> bool:
        return self.order("BUY_SEED", crop, n)

    def buy_animal(self, animal: str, n: int = 1) -> bool:
        return self.order("BUY_ANIMAL", animal, n)

    def buy_product(self, product: str, n: int = 1) -> bool:
        return self.order("BUY_PRODUCT", product, n)

    def sell(self, item: str, n: int = 1) -> bool:
        return n > 0 and self.order("SELL", item, n)

    def hire(self, n: int = 1) -> bool:
        return self.order("HIRE", n)

    def buy_land(self) -> bool:
        return self.order("BUY_LAND")

    def to_dict(self) -> dict:
        return {"farmer": self.farmer, "hands": self.hands, "market": self.market}
