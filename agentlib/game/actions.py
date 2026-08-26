"""Action construction and validation.

The env expects:
    {"farmer": [OP, *args], "hands": [[OP, *args], ...], "market": [[OP, *args], ...]}

`TurnPlan` is an optional builder — strategies may use it to avoid miscounting
hands or silently overflowing the market cap. `validate` is not optional: the
arbiter runs it on whatever a strategy returns, because a malformed action costs
the whole episode.
"""

from .config import MAX_MARKET_ORDERS_PER_TURN, MOVE_DELTA

PASS: list = ["PASS"]

# Market order priority. Procurement outranks disposal because a farm that can't
# buy seeds stops producing, whereas a delayed sale is only a timing loss.
PROCUREMENT = 0  # BUY_SEED, BUY_ANIMAL, BUY_PRODUCT (feed)
INVESTMENT = 1  # HIRE, BUY_LAND
DISPOSAL = 2  # SELL


def manhattan(a, b) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def adjacent(a, b) -> bool:
    return manhattan(a, b) == 1


def neighbors(pos):
    x, y = pos
    return [(x + dx, y + dy) for dx, dy in MOVE_DELTA.values()]


def move_toward(src, dst):
    """One step of greedy pathing. None if already there."""
    sx, sy = src
    dx, dy = dst
    if (sx, sy) == (dx, dy):
        return None
    # Longer axis first, so paths are L-shaped and predictable.
    if abs(dx - sx) >= abs(dy - sy) and dx != sx:
        return ["EAST"] if dx > sx else ["WEST"]
    if dy != sy:
        return ["SOUTH"] if dy > sy else ["NORTH"]
    return ["EAST"] if dx > sx else ["WEST"]


class TurnPlan:
    """Accumulator for one turn. Unit 0 is the main farmer; 1..n are hired hands."""

    def __init__(self, n_hands: int = 0):
        self.farmer: list = list(PASS)
        self.hands: list[list] = [list(PASS) for _ in range(n_hands)]
        self._market: list[tuple[int, int, list]] = []
        self._seq = 0

    def set_unit(self, idx: int, action: list) -> None:
        if idx == 0:
            self.farmer = list(action)
        elif 1 <= idx <= len(self.hands):
            self.hands[idx - 1] = list(action)

    def order(self, *parts, priority: int = DISPOSAL) -> None:
        """Queue a market order. Overflow past the cap is dropped by priority."""
        self._market.append((priority, self._seq, list(parts)))
        self._seq += 1

    def buy_seed(self, crop, n=1):
        self.order("BUY_SEED", crop, n, priority=PROCUREMENT)

    def buy_animal(self, animal, n=1):
        self.order("BUY_ANIMAL", animal, n, priority=PROCUREMENT)

    def buy_product(self, product, n=1):
        self.order("BUY_PRODUCT", product, n, priority=PROCUREMENT)

    def hire(self, n=1):
        self.order("HIRE", n, priority=INVESTMENT)

    def buy_land(self):
        self.order("BUY_LAND", priority=INVESTMENT)

    def sell(self, item, n=1):
        if n > 0:
            self.order("SELL", item, n, priority=DISPOSAL)

    def to_dict(self) -> dict:
        ordered = [o for _, _, o in sorted(self._market)]
        return {
            "farmer": self.farmer,
            "hands": self.hands,
            "market": ordered[:MAX_MARKET_ORDERS_PER_TURN],
        }


def validate(action, n_hands: int = 0) -> dict:
    """Coerce a strategy's return value into a legal action dict.

    Lenient where the intent is clear, strict where it isn't — raises TypeError
    or ValueError so the arbiter can fall back to the default strategy rather
    than submit something the env will reject.
    """
    if not isinstance(action, dict):
        raise TypeError(f"action must be a dict, got {type(action).__name__}")

    farmer = action.get("farmer") or PASS
    if isinstance(farmer, str):
        farmer = [farmer]
    if not isinstance(farmer, list) or not farmer:
        raise ValueError(f"bad farmer action: {action.get('farmer')!r}")

    hands = action.get("hands") or []
    if not isinstance(hands, list):
        raise TypeError(f"hands must be a list, got {type(hands).__name__}")
    hands = [[h] if isinstance(h, str) else list(h) for h in hands if h]
    # More hand actions than hands is harmless (env ignores the tail) but signals
    # a bookkeeping bug, so trim rather than pass it through.
    if n_hands:
        hands = hands[:n_hands]

    market = action.get("market") or []
    if not isinstance(market, list):
        raise TypeError(f"market must be a list, got {type(market).__name__}")
    market = [list(o) for o in market if isinstance(o, (list, tuple)) and o]

    return {
        "farmer": list(farmer),
        "hands": hands,
        "market": market[:MAX_MARKET_ORDERS_PER_TURN],
    }
