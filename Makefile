PY := .venv/bin/python
KAGGLE := .venv/bin/kaggle
COMP := kaggriculture
MSG ?= dev run

.PHONY: setup match arena bundle submit status leaderboard test lint clean

setup:
	./setup.sh

## Play one local game: make match OPP=random
match:
	$(PY) tools/run_match.py --opponent $(or $(OPP),random)

## Play N games vs a baseline: make arena OPP=starter N=20
arena:
	$(PY) tools/arena.py --opponent $(or $(OPP),random) --games $(or $(N),20)

## Build submission.tar.gz with main.py at the root
bundle:
	$(PY) tools/bundle.py

submit: bundle
	$(KAGGLE) competitions submit $(COMP) -f submissions/submission.tar.gz -m "$(MSG)"

status:
	$(KAGGLE) competitions submissions $(COMP)

leaderboard:
	$(KAGGLE) competitions leaderboard $(COMP) -s

test:
	$(PY) -m pytest -q

lint:
	.venv/bin/ruff check src tools tests

clean:
	rm -rf __pycache__ .pytest_cache submissions/*.tar.gz
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
