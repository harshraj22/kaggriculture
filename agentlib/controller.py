"""The Controller interface.

A controller answers one question per turn: given the eligible strategies, who
drives? It is consulted EVERY turn — if an implementation wants to be sticky,
that's its own business to implement, because only it knows whether its answer
can change between turns.

Implementations live in `agentlib/controllers/`.
"""

from abc import ABC, abstractmethod

from .observation import Obs
from .strategy import Strategy


class Controller(ABC):
    #: Registry key, and the `type:` value in a config file.
    type: str = "base"

    @abstractmethod
    def select(self, obs: Obs, candidates: list[Strategy]) -> Strategy | None:
        """Pick from `candidates`, already filtered to eligible strategies.

        Return None to defer to the arbiter's built-in default.
        """

    def describe(self) -> dict:
        """Serialisable summary, recorded in experiment results."""
        return {"type": self.type}
