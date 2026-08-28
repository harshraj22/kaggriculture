"""The Controller interface.

A controller answers one question per turn: given the eligible strategies, who
drives? It is consulted EVERY turn — if an implementation wants to be sticky,
that's its own business, because only it knows whether its answer can change.

`select` receives the full observation, so a controller is free to be as clever
as it likes: money, opponent money, market prices and inventory, shed contents,
tiles, day and hour are all available. `ScheduleController` happens to look at
nothing but the turn number; nothing requires that.

Controllers may hold state across turns — one instance is built per episode and
`reset()` clears it. Anything accumulated in `select` (price history, switch
counts, regime estimates) is fair game.

To add one:

1. Subclass `Controller`, set `type`, implement `from_spec` and `select`.
2. Register it in `controllers/__init__.py`.

That's the whole contract. Nothing in the arbiter, the strategies, or the
evaluation harness needs to know it exists.
"""

from abc import ABC, abstractmethod

from ..game.observation import Obs
from ..strategies.base import Strategy


class Controller(ABC):
    #: Registry key, and the `type:` value in a config file.
    type: str = "base"

    @classmethod
    def from_spec(cls, spec: dict, known: set[str] | None = None, strict: bool = True):
        """Build from a resolved config spec.

        `known` is the set of registered strategy names, for validating any the
        config references. `strict` distinguishes a sweep (where a typo must be
        fatal) from an episode (where it must degrade). Raise `ConfigError` for
        anything a config could plausibly get wrong.

        The default ignores the spec, which suits controllers with no options.
        """
        return cls()

    def reset(self) -> None:
        """Clear per-episode state. Called by `Agent.reset()`.

        Stateless controllers ignore this. Stateful ones must implement it, or
        episode N+1 inherits episode N's state and every measurement after the
        first is quietly contaminated.
        """

    @abstractmethod
    def select(self, obs: Obs, candidates: list[Strategy]) -> Strategy | None:
        """Pick from `candidates`, already filtered to eligible strategies.

        Return None to defer to the arbiter's built-in default.
        """

    def describe(self) -> dict:
        """Serialisable **static** summary — what was configured.

        Must not include per-episode counters. `evaluate.py` calls this on a
        controller built in the parent process purely to validate the spec; that
        object never plays a turn, so any runtime state it reports is whatever it
        was constructed with. Runtime belongs in `diagnostics()`.
        """
        return {"type": self.type}

    def diagnostics(self) -> dict:
        """Per-episode runtime counters, collected from the process that played.

        Returned per episode and aggregated across the run, so "this rule never
        fired once in 60 episodes" is visible in the result row instead of being
        something you find out after a sweep has spent its budget on it.

        Values must be numbers or lists of numbers so they can be summed.
        """
        return {}
