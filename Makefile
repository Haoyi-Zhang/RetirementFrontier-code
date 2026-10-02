PYTHON ?= python3

.PHONY: test reproduce core posix validate

test:
	$(PYTHON) scripts/run_tests.py -v

reproduce:
	$(PYTHON) scripts/reproduce_portable.py --require-smt
	$(PYTHON) scripts/aggregate.py

core:
	$(PYTHON) scripts/reproduce_portable.py --without-smt
	$(PYTHON) scripts/aggregate.py

posix:
	$(PYTHON) scripts/run_posix_smoke.py

validate:
	$(PYTHON) scripts/validate_release.py --run-tests
