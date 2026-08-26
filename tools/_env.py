"""Load `.env` for local development.

**Only `tools/` may call this. `agentlib/` must never load a .env.**

The agent runs inside Kaggle's sandbox, where python-dotenv doesn't exist and no
`.env` is present. If `agentlib` read one, local runs and submissions would
resolve config differently — the same class of bug as `__file__` (worked locally,
errored every submission) and `ACTIVE_CONFIG.exists()` (worked locally, silently
fell back to the builtin in the tarball). `tests/test_pipeline.py` asserts
`agentlib` imports no dotenv.

Real environment variables always win: `KAGGRICULTURE_CONFIG=x make eval` should
override whatever the file says, not the other way round.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def load_env(path=ENV_FILE) -> bool:
    """Load `.env` if present. Returns True if anything was loaded.

    Degrades to a no-op with a clear message when python-dotenv isn't installed,
    since it's a developer convenience and nothing depends on it.
    """
    path = Path(path)
    if not path.exists():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        print(f"[tools] {path.name} found but python-dotenv is not installed; ignoring it.")
        print("[tools] pip install python-dotenv  (or export the vars yourself)")
        return False

    # override=False: an explicit `VAR=x command` beats the file.
    return load_dotenv(path, override=False)


def describe() -> str:
    """One line naming which knobs are currently set, for tool banners."""
    keys = [k for k in sorted(os.environ) if k.startswith("KAGGRICULTURE_")]
    return ", ".join(f"{k.removeprefix('KAGGRICULTURE_').lower()}={os.environ[k]}" for k in keys)
