#!/usr/bin/env python
"""Build submissions/submission.tar.gz with main.py at the archive root.

Verifies the bundle by importing it from a temp dir before writing, so we never
burn one of the 5 daily submissions on a packaging mistake.
"""

import os
import subprocess
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "submissions")
OUT = os.path.join(OUT_DIR, "submission.tar.gz")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_env

load_env()

INCLUDE = ["main.py", "agentlib", "configs"]
EXCLUDE_SUFFIX = (".pyc", ".yaml", ".yml")  # YAML stays home; only .json ships
EXCLUDE_DIRS = {"__pycache__"}


def _filter(info: tarfile.TarInfo):
    name = os.path.basename(info.name)
    if name in EXCLUDE_DIRS or info.name.endswith(EXCLUDE_SUFFIX):
        return None
    return info


def compile_configs() -> list[str]:
    """Compile configs/*.yaml to sibling .json.

    The submission then needs only stdlib `json`. PyYAML is very likely present
    in Kaggle's image, but "very likely present" is exactly what `__file__` was,
    and that cost every submission.
    """
    import json as _json

    import yaml

    written = []
    for src in sorted(os.listdir(os.path.join(ROOT, "configs"))):
        if not src.endswith((".yaml", ".yml")):
            continue
        path = os.path.join(ROOT, "configs", src)
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        out = os.path.splitext(path)[0] + ".json"
        with open(out, "w") as f:
            _json.dump(data, f, indent=2, sort_keys=True)
        written.append(os.path.relpath(out, ROOT))
    return written


def build() -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    for rel in compile_configs():
        print(f"  compiled {rel}")
    with tarfile.open(OUT, "w:gz") as tar:
        for item in INCLUDE:
            path = os.path.join(ROOT, item)
            if not os.path.exists(path):
                raise SystemExit(f"missing {item}")
            tar.add(path, arcname=item, filter=_filter)
    return OUT


# Mirrors kaggle_environments/agent.py::get_last_callable. Deliberately NOT
# `import main`: the real loader execs the source with empty globals (so there is
# no __file__) and takes the LAST callable in module order. `import main` hides
# both failure modes, and both cost an entire episode.
_SMOKE = r'''
import os, sys
src = open("main.py").read()
sys.path.append(os.getcwd())          # what the loader does before exec
env = {}
exec(compile(src, "main.py", "exec"), env)
callables = [v for v in env.values() if callable(v)]
assert callables, "main.py defines no callable"
agent = callables[-1]
assert getattr(agent, "__name__", None) == "agent", (
    f"last callable is {getattr(agent, '__name__', agent)!r}, not 'agent' — "
    "the loader would call the wrong function"
)
obs = {"player": 0, "day": 0, "hour": 0, "step": 0,
       "farms": [{"money": 3000, "tiles": [[None]], "farmer": [0, 0], "hands": []},
                 {"money": 3000, "tiles": [[None]], "farmer": [0, 0], "hands": []}],
       "market": {"prices": {}, "inventory": {}}, "town": {"unlocked_shops": []},
       "private": {"shed": {}, "seeds": {}, "inventories": [{}]}}
r = agent(obs)
assert isinstance(r, dict) and "farmer" in r, r
print("smoke ok:", r)
'''


def verify(archive: str) -> None:
    """Extract to a temp dir and load it the way the env's agent loader would."""
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive) as tar:
            tar.extractall(tmp)
        if not os.path.exists(os.path.join(tmp, "main.py")):
            raise SystemExit("main.py is not at the archive root")
        proc = subprocess.run(
            [sys.executable, "-c", _SMOKE], cwd=tmp, capture_output=True, text=True, check=False
        )
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
            raise SystemExit("bundle failed its smoke test")
        print(proc.stdout.strip())


def activate(config: str) -> str:
    """Write configs/active.yaml — the config a SUBMISSION will actually run.

    Kaggle's runner sets no environment variables, so this file is the only way a
    chosen config reaches a submitted agent. Without it the agent silently falls
    back to the builtin controller no matter what the optimiser found.

    YAML is what lands in git, so "what does the current submission play?" is
    answered by reading a file rather than decoding JSON. The `.json` twin is
    written at the same moment — never separately — so the two cannot drift, and
    it's the only one that ships.
    """
    import json as _json

    import yaml

    sys.path.insert(0, ROOT)
    from agentlib.controllers import build_controller
    from agentlib.settings import load_spec
    from agentlib.strategies import build_all

    # Strict: activating a broken config would ship a silent fallback.
    spec = load_spec(config, strict=True)
    build_controller(spec, known={s.name for s in build_all()}, strict=True)

    payload = {k: v for k, v in spec.items() if not k.startswith("_")}
    out = os.path.join(ROOT, "configs", "active.yaml")
    header = (
        "# GENERATED by tools/bundle.py --activate. Do not edit by hand.\n"
        f"# source: {config}\n"
        "#\n"
        "# This is the config a SUBMISSION runs. Kaggle sets no env vars, so it is\n"
        "# the only channel by which a chosen config reaches a submitted agent.\n"
    )
    with open(out, "w") as f:
        f.write(header)
        yaml.safe_dump(payload, f, sort_keys=True, default_flow_style=False)

    # Compiled twin, written together so they can't diverge. Only this one ships:
    # the submission then needs stdlib `json`, not PyYAML.
    with open(os.path.join(ROOT, "configs", "active.json"), "w") as f:
        _json.dump(payload, f, indent=2, sort_keys=True)
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--activate",
        metavar="CONFIG",
        help="set configs/active.yaml from CONFIG before bundling (what the submission runs)",
    )
    args = ap.parse_args()

    if args.activate:
        print(f"  activated {os.path.relpath(activate(args.activate), ROOT)}"
              f"  <- {args.activate}")

    active = os.path.join(ROOT, "configs", "active.yaml")
    if not os.path.exists(active):
        print("WARNING: no configs/active.yaml — this submission will run the builtin")
        print("         controller, not a measured config. Use:")
        print("           python tools/bundle.py --activate configs/<best>.yaml")

    archive = build()
    verify(archive)
    size = os.path.getsize(archive) / 1024
    print(f"{archive}  ({size:.1f} KB)")
    print('submit with:  make submit MSG="your message"')
