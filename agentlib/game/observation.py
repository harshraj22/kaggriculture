"""Thin, defensive wrappers over the raw observation dict.

The env hands agents plain dicts (sometimes Struct-like objects). Everything here
uses .get() with sane defaults so a shape surprise degrades to PASS instead of
crashing the episode — a crashed agent scores zero.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .config import TURNS_PER_DAY


@dataclass
class Tile:
    x: int
    y: int
    raw: Any

    @property
    def locked(self) -> bool:
        return self.raw == "LOCKED"

    @property
    def empty(self) -> bool:
        return self.raw is None

    @property
    def kind(self) -> str | None:
        return self.raw.get("kind") if isinstance(self.raw, dict) else None

    @property
    def is_plant(self) -> bool:
        return self.kind == "PLANT"

    @property
    def is_weed(self) -> bool:
        return self.kind == "WEED"

    @property
    def is_structure(self) -> bool:
        return self.kind in ("COOP", "PASTURE")

    @property
    def has_animal(self) -> bool:
        return self.is_structure and self.raw.get("animal") is not None

    def get(self, key: str, default=None):
        return self.raw.get(key, default) if isinstance(self.raw, dict) else default

    @property
    def pos(self) -> tuple[int, int]:
        return (self.x, self.y)


class Obs:
    """Convenience view over one turn's observation."""

    def __init__(self, obs: dict):
        self.raw = obs
        self.player: int = obs.get("player", 0)
        self.day: int = obs.get("day", 0)
        self.hour: int = obs.get("hour", 0)
        self.step: int = obs.get("step", self.day * TURNS_PER_DAY + self.hour)

    # --- farms ---
    @property
    def me(self) -> dict:
        return self.raw.get("farms", [{}, {}])[self.player]

    @property
    def opponent(self) -> dict:
        farms = self.raw.get("farms", [{}, {}])
        return farms[1 - self.player] if len(farms) > 1 else {}

    @property
    def money(self) -> float:
        return self.me.get("money", 0)

    @property
    def opponent_money(self) -> float:
        return self.opponent.get("money", 0)

    @property
    def farmer(self) -> tuple[int, int]:
        fx, fy = self.me.get("farmer", [0, 0])
        return int(fx), int(fy)

    @property
    def hands(self) -> list[tuple[int, int]]:
        return [(int(x), int(y)) for x, y in self.me.get("hands", [])]

    @property
    def hires_today(self) -> int:
        return self.me.get("hires_today", 0)

    @property
    def unlocked_quadrants(self) -> list[str]:
        return list(self.me.get("unlocked_quadrants", []))

    # --- tiles ---
    @property
    def grid(self) -> list[list[Any]]:
        return self.me.get("tiles", [])

    def tile(self, x: int, y: int) -> Tile | None:
        g = self.grid
        if 0 <= y < len(g) and 0 <= x < len(g[y]):
            return Tile(x, y, g[y][x])
        return None

    def tiles(self) -> Iterator[Tile]:
        for y, row in enumerate(self.grid):
            for x, raw in enumerate(row):
                yield Tile(x, y, raw)

    def owned_tiles(self) -> Iterator[Tile]:
        for t in self.tiles():
            if not t.locked:
                yield t

    # --- private ---
    @property
    def private(self) -> dict:
        return self.raw.get("private", {})

    @property
    def shed(self) -> dict:
        return self.private.get("shed", {}) or {}

    @property
    def seeds(self) -> dict:
        return self.private.get("seeds", {}) or {}

    @property
    def inventories(self) -> list[dict]:
        return self.private.get("inventories", []) or [{}]

    @property
    def farmer_inventory(self) -> dict:
        inv = self.inventories
        return inv[0] if inv else {}

    def shed_used(self) -> int:
        return sum(self.shed.values())

    # --- market / town ---
    @property
    def prices(self) -> dict:
        return self.raw.get("market", {}).get("prices", {}) or {}

    @property
    def market_inventory(self) -> dict:
        return self.raw.get("market", {}).get("inventory", {}) or {}

    @property
    def unlocked_shops(self) -> list[str]:
        return list(self.raw.get("town", {}).get("unlocked_shops", []))

    # --- derived ---
    @property
    def days_left(self) -> int:
        from .config import DAYS

        return DAYS - self.day

    @property
    def is_last_day(self) -> bool:
        return self.days_left <= 1

    @property
    def shed_tiles(self) -> list[tuple[int, int]]:
        """The tiles a unit must stand on to DROP or PICKUP.

        Fixed by board size, not discovered: (4,4) (5,4) (4,5) (5,5) on a 10x10 board.
        Scanning `tiles` for a shed finds nothing — it isn't a tile kind. An earlier
        version of this class tried to infer the position from where units spawn at
        hour 0, which is right by luck and wrong in principle.
        """
        from .config import shed_access_tiles

        return [(int(x), int(y)) for x, y in shed_access_tiles(len(self.grid) or 10)]

    def at_shed(self, pos) -> bool:
        """Whether `pos` can DROP/PICKUP this turn."""
        return (int(pos[0]), int(pos[1])) in set(self.shed_tiles)
