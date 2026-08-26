"""Schedule controller — turn number decides the strategy.

    type: schedule
    schedule:
      - { from_day: 0,  to_day: 9,  strategy: wheat_loop }
      - { from_day: 10, to_day: 29, strategy: safe_farmer }

Rules use `from_day`/`to_day` or `from_turn`/`to_turn`; bounds are inclusive and
first match wins. The result is a pure function of turn number, so the controller
is consulted every turn and boundaries land exactly where the config says.

If the matched strategy is ineligible — or no rule matches — the arbiter's code
level default takes over. The schedule proposes; eligibility disposes.
"""

from ..config import DAYS, TURNS_PER_DAY
from ..controller import Controller
from ..observation import Obs
from ..settings import ConfigError
from ..strategy import Strategy

_RULE_KEYS = {"from_day", "to_day", "from_turn", "to_turn", "strategy"}


class Rule:
    __slots__ = ("from_turn", "strategy", "to_turn")

    def __init__(self, from_turn: int, to_turn: int, strategy: str):
        self.from_turn = from_turn
        self.to_turn = to_turn
        self.strategy = strategy

    def matches(self, step: int) -> bool:
        return self.from_turn <= step <= self.to_turn

    def as_dict(self) -> dict:
        return {
            "from_turn": self.from_turn,
            "to_turn": self.to_turn,
            "strategy": self.strategy,
        }


def _parse_rule(raw, idx: int, strict: bool) -> Rule:
    if not isinstance(raw, dict):
        raise ConfigError(f"schedule[{idx}] must be a mapping, got {type(raw).__name__}")

    unknown = set(raw) - _RULE_KEYS
    if unknown and strict:
        raise ConfigError(f"schedule[{idx}] has unknown keys: {sorted(unknown)}")

    strategy = raw.get("strategy")
    if not isinstance(strategy, str) or not strategy:
        raise ConfigError(f"schedule[{idx}] needs a 'strategy' name")

    has_day = "from_day" in raw or "to_day" in raw
    has_turn = "from_turn" in raw or "to_turn" in raw
    if has_day and has_turn:
        raise ConfigError(f"schedule[{idx}] mixes day and turn bounds; pick one")

    if has_turn:
        lo = int(raw.get("from_turn", 0))
        hi = int(raw.get("to_turn", DAYS * TURNS_PER_DAY - 1))
    else:
        lo = int(raw.get("from_day", 0)) * TURNS_PER_DAY
        hi = (int(raw.get("to_day", DAYS - 1)) + 1) * TURNS_PER_DAY - 1

    if hi < lo:
        raise ConfigError(f"schedule[{idx}] ends ({hi}) before it starts ({lo})")
    return Rule(lo, hi, strategy)


class ScheduleController(Controller):
    type = "schedule"

    def __init__(self, rules: list[Rule]):
        self.rules = rules

    @classmethod
    def from_spec(cls, spec: dict, known: set[str] | None = None, strict: bool = True):
        raw_rules = spec.get("schedule")
        if not isinstance(raw_rules, list) or not raw_rules:
            raise ConfigError("schedule controller needs a non-empty 'schedule' list")

        rules = [_parse_rule(r, i, strict) for i, r in enumerate(raw_rules)]

        if strict:
            if known is not None:
                for i, r in enumerate(rules):
                    if r.strategy not in known:
                        raise ConfigError(
                            f"schedule[{i}] references unknown strategy {r.strategy!r}; "
                            f"registered: {sorted(known)}"
                        )
            _assert_full_coverage(rules)

        return cls(rules)

    def select(self, obs: Obs, candidates: list[Strategy]) -> Strategy | None:
        if not candidates:
            return None
        by_name = {s.name: s for s in candidates}
        for rule in self.rules:
            if rule.matches(obs.step):
                hit = by_name.get(rule.strategy)
                if hit is not None:
                    return hit
                break  # scheduled but ineligible: let the caller's default take over
        return None

    def describe(self) -> dict:
        return {"type": self.type, "schedule": [r.as_dict() for r in self.rules]}

    def __repr__(self) -> str:
        return f"ScheduleController({len(self.rules)} rules)"


def _assert_full_coverage(rules: list[Rule]) -> None:
    """Reject gaps in strict mode.

    An uncovered range silently plays the default strategy. During a sweep that's
    an invisible confound — you'd credit a schedule for turns it never drove.
    """
    total = DAYS * TURNS_PER_DAY
    covered = [False] * total
    for rule in rules:
        for t in range(max(0, rule.from_turn), min(total, rule.to_turn + 1)):
            covered[t] = True

    gaps, start = [], None
    for t, seen in enumerate(covered):
        if not seen and start is None:
            start = t
        elif seen and start is not None:
            gaps.append((start, t - 1))
            start = None
    if start is not None:
        gaps.append((start, total - 1))

    if gaps:
        pretty = ", ".join(
            f"turns {a}-{b} (days {a // TURNS_PER_DAY}-{b // TURNS_PER_DAY})" for a, b in gaps
        )
        raise ConfigError(f"schedule leaves turns uncovered: {pretty}")
