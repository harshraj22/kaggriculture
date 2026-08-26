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

INCLUDE = ["main.py", "agentlib"]
EXCLUDE_SUFFIX = (".pyc",)
EXCLUDE_DIRS = {"__pycache__"}


def _filter(info: tarfile.TarInfo):
    name = os.path.basename(info.name)
    if name in EXCLUDE_DIRS or info.name.endswith(EXCLUDE_SUFFIX):
        return None
    return info


def build() -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
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


if __name__ == "__main__":
    archive = build()
    verify(archive)
    size = os.path.getsize(archive) / 1024
    print(f"{archive}  ({size:.1f} KB)")
    print("submit with:  make submit MSG=\"your message\"")
