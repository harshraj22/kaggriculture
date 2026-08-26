"""Config loading for controllers.

A config file describes ONE thing: which controller to build and how. The
strategy fallback (`SafeFarmer`) and the failure policy live in code and are
deliberately not configurable — no config, including one a Bayesian optimiser
invented, can weaken the safety net.

Two loading modes, because the requirements genuinely conflict:

* **strict** (tools, tests, optimiser) — a typo'd strategy name is a hard error.
  A silently-ignored key during a sweep would attribute a score to a config that
  wasn't actually running.
* **lenient** (inside an episode) — the same error must not zero a submission,
  so it logs loudly and falls back to the built-in default controller.

YAML is the authoring format. `bundle.py` compiles it to JSON, and the loader
prefers a sibling `.json` — so the submission needs no YAML parser, only stdlib.
"""

import json
import os
import traceback
from pathlib import Path

ENV_CONFIG = "KAGGRICULTURE_CONFIG"
ENV_CONTROLLER = "KAGGRICULTURE_CONTROLLER"
ENV_POLICY = "KAGGRICULTURE_POLICY"

#: Used when no config is supplied, or when a supplied one is unusable.
BUILTIN_SPEC: dict = {"type": "priority"}


class ConfigError(ValueError):
    """Raised in strict mode for anything a config could plausibly get wrong."""


def _read(path: Path) -> dict:
    if path.suffix == ".json":
        return json.loads(path.read_text())
    try:
        import yaml
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise ConfigError(
            f"{path} is YAML but PyYAML is unavailable. Run tools/bundle.py to "
            "compile configs to JSON, or install pyyaml."
        ) from e
    return yaml.safe_load(path.read_text()) or {}


def _resolve_path(path) -> Path | None:
    """Prefer a compiled .json sibling; that's what ships in the tarball."""
    if not path:
        return None
    p = Path(path)
    compiled = p.with_suffix(".json")
    if compiled.exists():
        return compiled
    return p if p.exists() else None


def load_spec(path=None, strict: bool = True) -> dict:
    """Read a controller spec. Env vars override arguments.

    `KAGGRICULTURE_CONTROLLER` overrides `type` even when a file is given, so a
    single config can be re-run under a different controller without editing it.
    """
    path = path or os.environ.get(ENV_CONFIG)
    resolved = _resolve_path(path)

    if path and resolved is None:
        msg = f"config not found: {path}"
        if strict:
            raise ConfigError(msg)
        print(f"[agentlib] {msg}; using builtin controller")
        spec = dict(BUILTIN_SPEC)
    elif resolved is None:
        spec = dict(BUILTIN_SPEC)
    else:
        try:
            spec = _read(resolved)
        except Exception as e:
            if strict:
                raise ConfigError(f"could not parse {resolved}: {e}") from e
            traceback.print_exc()
            print(f"[agentlib] falling back to builtin controller: {BUILTIN_SPEC}")
            spec = dict(BUILTIN_SPEC)

    if not isinstance(spec, dict):
        if strict:
            raise ConfigError(f"config must be a mapping, got {type(spec).__name__}")
        spec = dict(BUILTIN_SPEC)

    spec = dict(spec)
    spec.setdefault("type", BUILTIN_SPEC["type"])

    override = os.environ.get(ENV_CONTROLLER)
    if override:
        spec["type"] = override
    if os.environ.get(ENV_POLICY):
        spec.setdefault("policy", os.environ[ENV_POLICY])

    spec["_source"] = str(resolved) if resolved else "builtin"
    return spec


def spec_hash(spec: dict) -> str:
    """Stable hash of a *resolved* spec.

    Hashes content, not the file path, so renaming a config doesn't invent a new
    experiment and two identical configs collide as they should.
    """
    import hashlib

    payload = {k: v for k, v in spec.items() if not k.startswith("_")}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:12]
