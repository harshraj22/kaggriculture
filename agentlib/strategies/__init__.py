"""Strategy registry.

Add a strategy: define it, register it here, optionally rank it in DEFAULT_ORDER.
Nothing in the controller or the arbiter needs to change.
"""

from ..settings import ConfigError
from .base import Strategy
from .berry_farm import BerryFarm
from .market_farm import MarketFarm
from .melon_farm import MelonFarm
from .ranch_farm import RanchFarm
from .safe_farmer import SafeFarmer
from .wheat_farm import WheatFarm
from .wheat_loop import WheatLoop

REGISTRY: dict[str, type[Strategy]] = {
    SafeFarmer.name: SafeFarmer,
    WheatLoop.name: WheatLoop,
    WheatFarm.name: WheatFarm,
    MelonFarm.name: MelonFarm,
    RanchFarm.name: RanchFarm,
    BerryFarm.name: BerryFarm,
    MarketFarm.name: MarketFarm,
}

#: The fallback. Must be stateless and must not raise — see safe_farmer.py.
DEFAULT = SafeFarmer.name

#: Controller priority. Names omitted here rank last, in registration order.
#:
#: `market_farm` is deliberately absent: it is registered but unmeasured, and
#: adding it here would silently change what every `priority` config plays.
DEFAULT_ORDER: tuple[str, ...] = (
    RanchFarm.name,
    MelonFarm.name,
    WheatFarm.name,
    WheatLoop.name,
    SafeFarmer.name,
)


def build(name: str) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; have {sorted(REGISTRY)}")
    return REGISTRY[name]()


def apply_params(strategies, params, strict: bool = True) -> list[str]:
    """Override strategy tunables from a config's `params` block.

    Controllers have been configurable since the beginning; strategies have not,
    so every constant inside one could only be changed by editing the file. That
    is fine for a constant derived from the engine and useless for one that wants
    searching — and `tools/optimize.py` can only search what a spec can express.

        type: fixed
        strategy: market_farm
        params:
          market_farm:
            SATURATION: 1.2
            SELL_FLOOR_RATIO: 0.6

    Set as INSTANCE attributes over the class defaults, so two seats running the
    same strategy with different params in one process do not fight — the class
    object is shared between them and writing to it would not be a config, it
    would be a global.

    Only names that already exist on the class may be set, and only scalars. A
    typo in a param name is otherwise the quietest possible failure: the sweep
    runs, every trial is identical, and the flat response surface looks like a
    finding about the game rather than a mistake in the config.

    Returns the `"strategy.NAME"` keys applied, for the provenance record.
    """
    if not params:
        return []
    if not isinstance(params, dict):
        raise ConfigError(f"'params' must be a mapping, got {type(params).__name__}")

    by_name = {s.name: s for s in strategies}
    applied: list[str] = []
    for strategy_name, overrides in params.items():
        target = by_name.get(strategy_name)
        if target is None:
            msg = (f"params reference unknown strategy {strategy_name!r}; "
                   f"registered: {sorted(by_name)}")
            if strict:
                raise ConfigError(msg)
            print(f"[agentlib] {msg}; ignored")
            continue
        if not isinstance(overrides, dict):
            raise ConfigError(f"params[{strategy_name!r}] must be a mapping")
        tunable = _tunables(type(target))
        for key, value in overrides.items():
            # UPPER_CASE only, and it must already exist. That rules out
            # replacing `act` with a number or renaming the strategy out from
            # under the controller that selected it, both of which `hasattr`
            # alone happily allows.
            if key not in tunable:
                msg = (f"{strategy_name} has no tunable {key!r}; "
                       f"have {sorted(tunable)}")
                if strict:
                    raise ConfigError(msg)
                print(f"[agentlib] {msg}; ignored")
                continue
            if not isinstance(value, (int, float, str, bool, tuple, list)):
                raise ConfigError(
                    f"params[{strategy_name!r}][{key!r}] must be a scalar or "
                    f"sequence, got {type(value).__name__}"
                )
            setattr(target, key, tuple(value) if isinstance(value, list) else value)
            applied.append(f"{strategy_name}.{key}")
    return applied


def _tunables(cls) -> set[str]:
    """UPPER_CASE class attributes — the naming convention IS the contract."""
    return {name for name in dir(cls)
            if name.isupper() and not name.startswith("_")}


def build_all() -> list[Strategy]:
    return [cls() for cls in REGISTRY.values()]


def default_strategy() -> Strategy:
    return build(DEFAULT)


__all__ = [
    "DEFAULT",
    "DEFAULT_ORDER",
    "REGISTRY",
    "BerryFarm",
    "MarketFarm",
    "MelonFarm",
    "RanchFarm",
    "SafeFarmer",
    "Strategy",
    "WheatFarm",
    "WheatLoop",
    "apply_params",
    "build",
    "build_all",
    "default_strategy",
]
