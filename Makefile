PY      := $(or $(PYTHON),.venv/bin/python)
RUFF    := $(or $(RUFFBIN),.venv/bin/ruff)
KAGGLE  := .venv/bin/kaggle
COMP    := kaggriculture
SRC     := agentlib tools tests main.py
MSG     ?= dev run

.PHONY: help setup deps deps-upgrade test lint fix match eval eval-strategy eval-all compare wandb wandb-push activate bundle submit status leaderboard clean

help:
	@grep -E '^##' Makefile | sed 's/^## //'

## setup           create .venv and install dependencies
setup:
	./setup.sh

## deps            refresh .venv from requirements.txt (run after a git pull)
deps:
	$(PY) -m pip install -r requirements.txt
	$(PY) -m pip install --no-deps kaggle-environments

## deps-upgrade    UPGRADE kaggle-environments — changes game rules, invalidates results
deps-upgrade:
	@echo "This can change the game itself (1.32.7 altered CARROT/TOMATO/EGG curves)."
	@echo "Every prior measurement becomes incomparable. Re-run your baselines after."
	$(PY) -m pip install --no-deps --upgrade kaggle-environments
	@$(PY) -c "import kaggle_environments as k; print('now on', k.__version__)"

## test            run the unit tests (<1s, no env needed)
test:
	$(PY) -m pytest tests -q

## lint            ruff over agentlib, tools, tests, main.py
lint:
	$(RUFF) check $(SRC)

## fix             ruff --fix over the same
fix:
	$(RUFF) check --fix $(SRC)

## match           one local game:  make match OPP=starter
match:
	$(PY) tools/run_match.py --opponent $(or $(OPP),random) $(if $(CONFIG),--config $(CONFIG))

## eval            score a config:  make eval CONFIG=configs/safe_only.yaml [SPLIT=holdout]
eval:
	$(PY) tools/evaluate.py --config $(or $(CONFIG),configs/baseline.yaml) --split $(or $(SPLIT),train)

## eval-strategy   score ONE strategy alone:  make eval-strategy S=wheat_loop
eval-strategy:
	$(PY) tools/evaluate.py --strategy $(S) --split $(or $(SPLIT),train)

## eval-all        score every config in configs/ AND every strategy alone
eval-all:
	@for c in configs/*.yaml; do $(PY) tools/evaluate.py --config $$c --split $(or $(SPLIT),train); done
	@$(PY) -c "import sys; sys.path.insert(0,'.'); from agentlib.strategies import REGISTRY; print(' '.join(sorted(REGISTRY)))" \
	  | xargs -n1 -I{} $(PY) tools/evaluate.py --strategy {} --split $(or $(SPLIT),train)

## compare         tabulate results/experiments.jsonl
compare:
	$(PY) tools/compare.py

## wandb           backfill every result into Weights & Biases
wandb:
	$(PY) tools/sync_wandb.py

## wandb-push      upload runs recorded with WANDB_MODE=offline
wandb-push:
	.venv/bin/wandb sync --include-offline --mark-synced

## activate        choose what a SUBMISSION runs:  make activate CONFIG=configs/safe_only.yaml
activate:
	$(PY) tools/bundle.py --activate $(CONFIG)

## bundle          build + smoke-test submissions/submission.tar.gz
bundle:
	$(PY) tools/bundle.py

## submit          bundle, then upload:  make submit MSG="melon v2"
submit: bundle
	$(KAGGLE) competitions submit $(COMP) -f submissions/submission.tar.gz -m "$(MSG)"

## status          your recent submissions
status:
	$(KAGGLE) competitions submissions $(COMP)

## leaderboard     current standings
leaderboard:
	$(KAGGLE) competitions leaderboard $(COMP) -s

## clean           remove caches, compiled configs and built archives
clean:
	rm -rf .pytest_cache .ruff_cache submissions/*.tar.gz configs/*.json
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
