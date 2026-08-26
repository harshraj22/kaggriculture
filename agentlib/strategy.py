"""The Strategy interface.

A strategy is stateful across turns within an episode — the same instance is
reused every turn, selected or not. That's the point: a strategy that only runs
occasionally still needs an accurate picture of the world when its turn comes.

Only `name` and `act` are required. `observe` and `on_action` no-op by default,
so a stateless strategy inherits the right behaviour by writing nothing.
"""

from abc import ABC, abstractmethod

from .observation import Obs


class Strategy(ABC):
    #: Unique key. Used by the registry, the controller's priority order, and the journal.
    name: str = "unnamed"

    def on_episode_start(self) -> None:
        """Reset per-episode state. Called once before the first turn."""

    def observe(self, obs: Obs) -> None:
        """Update internal state from the world.

        Called EVERY turn on EVERY strategy, whether or not this one is acting.
        Stateless strategies ignore it.
        """

    def is_eligible(self, obs: Obs) -> bool:
        """Whether this strategy is willing to drive right now.

        Keeps applicability knowledge in the strategy instead of the controller,
        so adding a strategy never means editing the controller. Doubles as the
        action mask if the controller is later replaced by a learned policy.
        """
        return True

    @abstractmethod
    def act(self, obs: Obs) -> dict:
        """Return this turn's action: {"farmer": [...], "hands": [...], "market": [...]}."""

    def on_action(self, obs: Obs, action: dict, chosen: str) -> None:
        """Reconcile against what actually happened.

        Called EVERY turn on EVERY strategy, with the action that was committed
        and the name of the strategy that produced it. This is how a strategy
        that wasn't selected discovers its plan is stale.
        """
