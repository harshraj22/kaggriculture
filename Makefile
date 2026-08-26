PY      := $(or $(PYTHON),.venv/bin/python)
RUFF    := $(or $(RUFFBIN),.venv/bin/ruff)
KAGGLE  := .venv/bin/kaggle
COMP    := kaggriculture
SRC     := agentlib tools tests main.py
MSG     ?= dev run

.PHONY: help setup test lint fix match arena eval eval-all compare bundle submit status leaderboard clean

help:
	@grep -E '^##' Makefile | sed 's/^## //'

## setup           create .venv and install dependencies
setup:
	./setup.sh

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

## arena           quick N-game win rate:  make arena OPP=starter N=20
arena:
	$(PY) tools/arena.py --opponent $(or $(OPP),random) --games $(or $(N),20)

## eval            score a config:  make eval CONFIG=configs/safe_only.yaml [SPLIT=holdout]
eval:
	$(PY) tools/evaluate.py --config $(or $(CONFIG),configs/baseline.yaml) --split $(or $(SPLIT),train)

## eval-all        score every config in configs/
eval-all:
	@for c in configs/*.yaml; do $(PY) tools/evaluate.py --config $$c --split $(or $(SPLIT),train); done

## compare         tabulate results/experiments.jsonl
compare:
	$(PY) tools/compare.py

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
