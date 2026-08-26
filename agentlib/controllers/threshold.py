"""Threshold controller — game state decides the strategy, config supplies the numbers.

    type: threshold
    rules:
      - { when: { day_gte: 25 },         strategy: safe_farmer }
      - { when: { money_gte: 8000 },     strategy: wheat_loop }
      - { when: { behind_by_gte: 2000 }, strategy: wheat_loop }
      - { when: {},                      strategy: safe_farmer }   # catch-all

First matching rule wins; an empty `when` always matches. This is the shape most
worth optimising: the *meaning* of each condition is code, the *numbers* are
config. A Bayesian optimiser then searches over 25 / 8000 / 2000 — a continuous
space, which it handles far better than the categorical "which strategy in which
day-slot" that `schedule` offers.

It also exists as the reference for extending this package: it reads real game
state, carries per-episode state, and adding it required exactly two things —
this file, and one line in `controllers/__init__.py`.
"""

from ..game.observation import Obs
from ..settings import ConfigError
from ..strategies.base import Strategy
from .base import Controller

#: Condition name -> (obs -> value). Add an entry to extend the vocabulary; the
#: comparison is always `value >= threshold`, which keeps configs monotone and
#: therefore friendly to an optimiser.
PREDICATES = {
    "day_gte": lambda obs: obs.day,
    "turn_gte": lambda obs: obs.step,
    "money_gte": lambda obs: obs.money,
    "ahead_by_gte": lambda obs: obs.money - obs.opponent_money,
    "behind_by_gte": lambda obs: obs.opponent_money - obs.money,
    "shed_used_gte": lambda obs: obs.shed_used(),
    "hands_gte": lambda obs: len(obs.hands),
    "quadrants_gte": lambda obs: len(obs.unlocked_quadrants),
    "shops_gte": lambda obs: len(obs.unlocked_shops),
}


class ThresholdRule:
    __slots__ = ("conditions", "strategy")

    def __init__(self, conditions: dict, strategy: str):
        self.conditions = conditions
        self.strategy = strategy

    def matches(self, obs: Obs) -> bool:
        return all(PREDICATES[k](obs) >= v for k, v in self.conditions.items())

    def as_dict(self) -> dict:
        return {"when": dict(self.conditions), "strategy": self.strategy}


class ThresholdController(Controller):
    type = "threshold"

    def __init__(self, rules: list[ThresholdRule]):
        self.rules = rules
        self.reset()

    def reset(self) -> None:
        # Per-episode state. Surfaced in describe() so a result row shows how
        # much switching actually happened, not just what was configured.
        self.switches = 0
        self._last: str | None = None

    @classmethod
    def from_spec(cls, spec: dict, known: set[str] | None = None, strict: bool = True):
        raw = spec.get("rules")
        if not isinstance(raw, list) or not raw:
            raise ConfigError("threshold controller needs a non-empty 'rules' list")

        rules = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ConfigError(f"rules[{i}] must be a mapping")
            unknown = set(item) - {"when", "strategy"}
            if unknown and strict:
                raise ConfigError(f"rules[{i}] has unknown keys: {sorted(unknown)}")

            strategy = item.get("strategy")
            if not isinstance(strategy, str) or not strategy:
                raise ConfigError(f"rules[{i}] needs a 'strategy' name")
            if strict and known is not None and strategy not in known:
                raise ConfigError(
                    f"rules[{i}] references unknown strategy {strategy!r}; "
                    f"registered: {sorted(known)}"
                )

            conditions = item.get("when") or {}
            if not isinstance(conditions, dict):
                raise ConfigError(f"rules[{i}]['when'] must be a mapping")
            for key, value in conditions.items():
                if key not in PREDICATES:
                    raise ConfigError(
                        f"rules[{i}] uses unknown condition {key!r}; "
                        f"available: {sorted(PREDICATES)}"
                    )
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ConfigError(f"rules[{i}]['when'][{key!r}] must be a number")

            rules.append(ThresholdRule(conditions, strategy))

        if strict and rules[-1].conditions:
            raise ConfigError(
                "the last rule must be an unconditional catch-all ({'when': {}}); "
                "otherwise state outside every rule silently plays the default, "
                "which is an invisible confound in a sweep"
            )
        return cls(rules)

    def select(self, obs: Obs, candidates: list[Strategy]) -> Strategy | None:
        if not candidates:
            return None
        by_name = {s.name: s for s in candidates}
        for rule in self.rules:
            if rule.matches(obs):
                hit = by_name.get(rule.strategy)
                if hit is None:
                    break  # matched but ineligible: defer to the code default
                if hit.name != self._last:
                    self.switches += 1
                    self._last = hit.name
                return hit
        return None

    def describe(self) -> dict:
        return {
            "type": self.type,
            "rules": [r.as_dict() for r in self.rules],
            "switches": self.switches,
        }

    def __repr__(self) -> str:
        return f"ThresholdController({len(self.rules)} rules, {self.switches} switches)"
