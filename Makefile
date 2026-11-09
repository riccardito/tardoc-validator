PYTHON ?= python
CATALOGUE ?= Anhang_A2_Katalog_des_TARDOC_1.4c.xlsx

.PHONY: install demo eval eval-official load test lint

install:
	$(PYTHON) -m pip install -q -e ".[dev]"

demo:
	$(PYTHON) -m tardoc_validator.cli validate examples/invoices.json; true

eval:
	$(PYTHON) -m tardoc_validator.cli eval -n 1000

load:
	$(PYTHON) -m tardoc_validator.cli load $(CATALOGUE) -o rules/official/tardoc-1.4c.yaml

eval-official: load
	$(PYTHON) -m tardoc_validator.cli eval -n 1000 --rules rules/official/tardoc-1.4c.yaml

test:
	$(PYTHON) -m pytest --cov --cov-report=term-missing:skip-covered

lint:
	$(PYTHON) -m ruff check .
