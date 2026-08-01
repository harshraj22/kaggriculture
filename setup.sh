#!/usr/bin/env bash
# Create the local (macOS) virtualenv and install dependencies.
# Usage: ./setup.sh   then:  source .venv/bin/activate
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3.13}"
echo "==> Using $($PY --version)"

if [ ! -d .venv ]; then
  echo "==> Creating .venv"
  "$PY" -m venv .venv
fi

echo "==> Installing dependencies"
.venv/bin/python3 -m pip install --upgrade pip
.venv/bin/python3 -m pip install -r requirements.txt

echo "==> Freezing exact versions to requirements.lock.txt"
.venv/bin/python3 -m pip freeze > requirements.lock.txt

cat <<'EOF'

Done. Next steps:

  source .venv/bin/activate
  python3 tools/run_match.py --opponent random     # local game
  kaggle competitions list -s kaggriculture       # verify Kaggle auth

Kaggle auth: get a token at https://www.kaggle.com/settings/api, then
  mkdir -p ~/.kaggle && nano ~/.kaggle/access_token && chmod 600 ~/.kaggle/access_token
EOF
