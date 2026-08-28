"""Learned controller — STUB. Defines the seam, implements nothing.

The interface is already RL-shaped and that is the point of building it now:

* **Action space** — pick one of N registered strategies. A fixed discrete head.
* **Action mask** — `candidates` is the eligible subset. Strategies mask
  themselves via `is_eligible`, so the policy is never asked to reason about an
  illegal choice.
* **Observation** — `features(obs)` below, currently the scalars we already have.
  Deliberately not expanded: feature engineering before we know which strategies
  matter would be guessing.
* **Reward** — final money (the env's own reward). Credit assignment across a
  720-turn episode with one decision per turn is the hard part, unsolved here.
* **Trajectory** — `Agent.journal` already records (step, chosen, money) per turn.

Config:

    type: rl
    policy: models/policy.npz     # or KAGGRICULTURE_POLICY

No training code lives here. When we get to it, the trainer produces an artifact
and `predict` learns to read it; nothing else in the codebase needs to change.
"""

from ..game.observation import Obs
from ..strategies.base import Strategy
from .base import Controller

#: Bump on ANY change to `features()` — order, count, or meaning.
#:
#: Recorded into every trajectory. Without it, adding a ninth scalar silently
#: invalidates every episode collected before the change while the files still
#: load fine and the trainer still runs: the same class of bug `code_hash` guards
#: against in the results store, one level down and with no error to notice.
FEATURE_VERSION = 1

#: Names in order, so a trained policy's inputs stay interpretable and a shape
#: mismatch names the culprit instead of surfacing as a matrix error.
FEATURE_NAMES = (
    "day_frac", "hour_frac", "money", "opp_money",
    "money_delta", "shed_used", "n_hands", "quadrants",
)


def features(obs: Obs) -> list[float]:
    """Cheap scalars already present on the observation. No scans, no scaling."""
    from ..game.config import DAYS, SHED_CAPACITY, STARTING_MONEY, TURNS_PER_DAY

    return [
        obs.day / DAYS,
        obs.hour / TURNS_PER_DAY,
        obs.money / STARTING_MONEY,
        obs.opponent_money / STARTING_MONEY,
        (obs.money - obs.opponent_money) / STARTING_MONEY,
        obs.shed_used() / SHED_CAPACITY,
        len(obs.hands) / 10.0,
        len(obs.unlocked_quadrants) / 4.0,
    ]


class PolicyController(Controller):
    type = "rl"

    def __init__(self, policy_path=None):
        self.policy_path = policy_path
        self._policy = None

    @classmethod
    def from_spec(cls, spec: dict, known: set[str] | None = None, strict: bool = True):
        return cls(spec.get("policy"))

    def reset(self) -> None:
        """Keep the loaded policy; drop anything episode-scoped."""

    def load(self):
        """Load the trained artifact. Not implemented yet."""
        raise NotImplementedError(
            "The RL controller is a stub — no policy format or trainer exists yet.\n"
            "Use `type: schedule` (see configs/) or `type: priority` for now.\n"
            "When training lands, implement load()/predict() here; the rest of the "
            "codebase already provides the action mask, features and trajectories."
        )

    def select(self, obs: Obs, candidates: list[Strategy]) -> Strategy | None:
        if self._policy is None:
            self.load()  # raises, with the message above
        raise NotImplementedError  # pragma: no cover - unreachable until load() works

    def describe(self) -> dict:
        return {"type": self.type, "policy": self.policy_path}
