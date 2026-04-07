PYTHON ?= python3
PYCACHE_PREFIX ?= /tmp/pycache

.PHONY: run demo ops test compile ci

run:
	$(PYTHON) scripts/dev_runner.py --reset-data

demo:
	$(PYTHON) scripts/demo_flow.py

ops:
	$(PYTHON) scripts/ops_report.py

test:
	PYTHONPYCACHEPREFIX=$(PYCACHE_PREFIX) $(PYTHON) -m unittest tests.test_end_to_end -v

compile:
	PYTHONPYCACHEPREFIX=$(PYCACHE_PREFIX) $(PYTHON) -m compileall services shared scripts tests

ci: compile test
