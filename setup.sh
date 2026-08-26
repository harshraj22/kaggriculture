#!/usr/bin/env bash
# Create the local virtualenv and install dependencies.
# Usage: ./setup.sh          then:  source .venv/bin/activate
#        PYTHON=python3.12 ./setup.sh    to override the interpreter
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3.13}"

# 3.14 has no pygame wheel yet and several other packages lag; 3.13 is the
# known-good target. Not fatal, just noisy, since we skip pygame anyway.
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "error: '$PY' not found." >&2
  echo "  install it (brew install python@3.13 / pyenv install 3.13)," >&2
  echo "  or point at another one:  PYTHON=python3.12 ./setup.sh" >&2
  exit 1
fi

echo "==> Using $("$PY" --version)"

if [ ! -d .venv ]; then
  echo "==> Creating .venv"
  "$PY" -m venv .venv
fi

VPY=.venv/bin/python

echo "==> Upgrading pip"
$VPY -m pip install --quiet --upgrade pip

echo "==> Installing requirements.txt"
$VPY -m pip install --quiet -r requirements.txt

# Installed separately and WITHOUT dependencies on purpose. See the header of
# requirements.txt: the declared tree is several GB of torch/jax/transformers
# that kaggriculture never touches, and pygame fails to build on newer Pythons.
echo "==> Installing kaggle-environments (--no-deps)"
$VPY -m pip install --quiet --no-deps --upgrade kaggle-environments

echo "==> Verifying the kaggriculture environment loads"
if ! $VPY - <<'PY'
import sys
try:
    from kaggle_environments import make
except ImportError as e:
    sys.exit(f"cannot import kaggle_environments: {e}")
try:
    make("kaggriculture")
except Exception as e:  # noqa: BLE001
    sys.exit(
        f"make('kaggriculture') failed: {e}\n"
        "  ImportError  -> add the missing package to requirements.txt\n"
        "  InvalidArgument('Unknown Environment Specification')\n"
        "               -> installed kaggle-environments predates the env"
    )
print("    kaggriculture OK")
PY
then
  echo "==> Setup incomplete: see the error above." >&2
  exit 1
fi

echo "==> Freezing exact versions to requirements.lock.txt"
$VPY -m pip freeze > requirements.lock.txt

cat <<'EOF'

Done. Next steps:

  source .venv/bin/activate
  make test                                    # unit tests
  python tools/run_match.py --opponent starter # one local game
  kaggle competitions list -s kaggriculture    # verify Kaggle auth

Kaggle auth: get a token at https://www.kaggle.com/settings/api, then
  mkdir -p ~/.kaggle && nano ~/.kaggle/access_token && chmod 600 ~/.kaggle/access_token
EOF
