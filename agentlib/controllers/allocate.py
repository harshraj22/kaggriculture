"""AllocateController — split the UNITS between specialists, don't alternate them.

Every other controller answers "which one strategy drives this turn?". That
question has no good answer once strategies are single-domain specialists,
because the domains have **upkeep obligations**. Measured: switching from
`melon_farm` to `ranch_farm` on day 14 turned 36 live plants into 47 weeds and
drained 15,311 coins to 1,150. Crops need watering every day; walking away from
them does not pause the asset, it destroys it.

So this controller answers a different question: **which units does each
specialist get?** Both run every turn, each planning only for its own units, and
the arbiter concatenates the results.

    type: allocate
    shares:
      - { strategy: melon_farm, share: 0.6 }
      - { strategy: ranch_farm, share: 0.4 }
    ramp_days: 8          # optional: give everything to the first entry until then

## Why a ramp

A rancher's units are worthless before there are animals to tend, and animals
are bought out of realised profit. Handing four of nine units to `ranch_farm` on
day 0 idles them while the farm is still earning its first coins. `ramp_days`
gives every unit to the first entry until the money exists, then splits.

## Why shares rather than rules

`share` is a continuous parameter, which is what BO handles well — unlike "which
strategy in which day-slot", which is categorical and which the `schedule`
controller already covers. The split is also the honest form of the underlying
question: labour is the scarce resource, so dividing labour IS the decision.

## Measured: it works, and it does not pay

v1/train, 60 paired episodes, `ramp_days: 8`:

    melon 0.50 / ranch 0.50    +18,861
    melon 0.60 / ranch 0.40    +22,570
    melon 0.85 / ranch 0.15    +27,254
    melon 1.00 / ranch 0.00    +35,246   <- melon_farm alone
    (for reference: ranch_farm alone +32,804, day-14 SWITCH +2,501)

The mechanism is sound — allocation scores 22,570 where time-slicing the same
two strategies scored 2,501 — but the curve is **monotonic toward the degenerate
allocation**. Every unit handed to the second specialist costs more than it
returns.

The reason is that both strategies have **threshold effects, not smooth returns
to labour**. A farm below the crew size needed to water everything does not grow
proportionally less; its plants die (measured earlier: sowing past capacity
collapsed 50 live plants to 9 in five days). A ranch below the crew needed to
feed everything loses animals outright to escape. Splitting nine units puts both
below their viable minimum, so we pay two fixed costs and collect neither payoff.

So this controller is a real new capability that currently selects "give
everything to melon_farm". It is kept because the capability is what was
missing, not because the split is worth shipping — and because the next
specialist pair, or a larger labour budget, may sit on the other side of those
thresholds.

## Retested on specialists that do not compete — and it still loses

The reading above was that the split failed because `melon_farm` and
`ranch_farm` both had crop upkeep and overlapping market demands. `market_farm`
and the drain-sized `ranch_farm` address disjoint halves of the board (crops
~96k, animal products ~130k), so the split should finally have something to
divide. Measured against `opponents/v48`, 6 seeds x both seats:

    market_farm alone          27,425
    ranch_farm alone           22,686
    allocate 0.5 / 0.5         14,183
    allocate 0.6 / 0.4         14,093

Still worse than either alone, and the reason is new and specific.

## The blocker is the SHARED SHED, not the labour split

Both strategies act on one farm, so they see one shed — and they hold opposite,
individually correct intentions about the same item. `ranch_farm` buys wheat
because its herd eats it and holds a reserve back from sale; `market_farm` sees
wheat in the shed and sells it, because selling the shed is what a crop farm
does and it has no idea a herd exists. The pair loops: **6,045 wheat bought and
sold per episode**, paying the spread on every lap, and the ranch starves anyway.

Netting opposing orders inside `_dedupe_orders` was tried and is worth ~3 coins
in 14,000, because the buy and the sell fall on *different turns*. The real fix
is a shed **reservation** — a strategy declaring "this stock is spoken for" that
the arbiter honours before any other strategy's `_sell` sees it. That is the
"strategies need to negotiate" change this file has always said it could not
make, now with a concrete first case to design against.

## What it cannot do

It cannot make two strategies cooperate on the same tile, it cannot stop them
competing for cash, and — per the above — it cannot stop one selling what
another is holding. Each plans its own market orders and the arbiter merely
merges them, taking the largest HIRE rather than the sum.
"""

from ..game.observation import Obs
from ..settings import ConfigError
from ..strategies.base import Strategy
from .base import Controller


class AllocateController(Controller):
    type = "allocate"

    def __init__(self, shares: list[tuple[str, float]], ramp_days: int = 0):
        self.shares = shares
        self.ramp_days = int(ramp_days)
        self.reset()

    def reset(self) -> None:
        #: Unit-turns handed to each strategy, for the result row. A share that
        #: never materialises into units is the allocation equivalent of an
        #: unreachable threshold rule.
        self.granted: list[int] = [0] * len(self.shares)
        self.turns = 0

    @classmethod
    def from_spec(cls, spec: dict, known: set[str] | None = None, strict: bool = True):
        raw = spec.get("shares")
        if not isinstance(raw, list) or not raw:
            raise ConfigError("allocate controller needs a non-empty 'shares' list")

        shares: list[tuple[str, float]] = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ConfigError(f"shares[{i}] must be a mapping")
            name = item.get("strategy")
            if not isinstance(name, str) or not name:
                raise ConfigError(f"shares[{i}] needs a 'strategy' name")
            if strict and known is not None and name not in known:
                raise ConfigError(
                    f"shares[{i}] references unknown strategy {name!r}; "
                    f"registered: {sorted(known)}"
                )
            try:
                weight = float(item.get("share", 1.0))
            except (TypeError, ValueError):
                raise ConfigError(f"shares[{i}]['share'] must be a number") from None
            if weight < 0:
                raise ConfigError(f"shares[{i}]['share'] must not be negative")
            shares.append((name, weight))

        if sum(w for _, w in shares) <= 0:
            raise ConfigError("at least one share must be positive")
        return cls(shares, int(spec.get("ramp_days", 0) or 0))

    # --- the one method that matters -----------------------------------------

    def allocate(self, obs: Obs, candidates: list[Strategy]) -> dict | None:
        by_name = {s.name: s for s in candidates}
        live = [(by_name[n], w) for n, w in self.shares if n in by_name and w > 0]
        if not live:
            return None

        n_units = 1 + len(obs.hands)
        self.turns += 1

        # Before the ramp, everything goes to the first LIVE entry: a specialist
        # with nothing to tend yet would only idle the units it was given.
        if obs.day < self.ramp_days or len(live) == 1:
            # Credit the strategy that actually received them. Crediting slot 0
            # unconditionally is wrong whenever the first share is ineligible or
            # struck out, and it reads as the opposite of the truth: a run where
            # `market_farm` was disabled on day 6 and `ranch_farm` played the
            # other 23 days reported `granted: [4781, 4]`.
            self.granted[self._slot(live[0][0])] += n_units
            return {live[0][0]: list(range(n_units))}

        # Largest-remainder apportionment. The first version reserved one unit
        # for every remaining strategy, which with a small crew starved the
        # HIGHEST share: 0.6/0.4 over a single unit gave that unit entirely to
        # the 0.4 side. Floor-then-distribute-remainders has no such asymmetry
        # and is the standard way to split an integer resource by weight.
        total = sum(w for _, w in live)
        ideal = [n_units * w / total for _, w in live]
        counts = [int(x) for x in ideal]
        for idx in sorted(range(len(live)), key=lambda i: ideal[i] - counts[i],
                          reverse=True)[:n_units - sum(counts)]:
            counts[idx] += 1

        out: dict[Strategy, list[int]] = {}
        cursor = 0
        for idx, (strategy, _) in enumerate(live):
            take = counts[idx]
            if take <= 0:
                continue
            out[strategy] = list(range(cursor, cursor + take))
            cursor += take
            self.granted[self._slot(strategy)] += take
        return out or None

    def _slot(self, strategy: Strategy) -> int:
        """Index of `strategy` in `shares`. `live` skips ineligible entries, so
        its indices and `shares`' indices are not the same list."""
        for i, (name, _w) in enumerate(self.shares):
            if name == strategy.name:
                return i
        return 0

    def select(self, obs: Obs, candidates: list[Strategy]) -> Strategy | None:
        """Only reached if `allocate` returns nothing — then behave as a fallback."""
        by_name = {s.name: s for s in candidates}
        for name, _ in self.shares:
            if name in by_name:
                return by_name[name]
        return None

    def describe(self) -> dict:
        return {
            "type": self.type,
            "shares": [{"strategy": n, "share": w} for n, w in self.shares],
            "ramp_days": self.ramp_days,
        }

    def diagnostics(self) -> dict:
        return {"granted": list(self.granted), "turns": self.turns}

    def __repr__(self) -> str:
        return f"AllocateController({self.shares}, ramp={self.ramp_days})"
