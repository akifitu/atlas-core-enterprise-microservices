PYTHON ?= python3
PYCACHE_PREFIX ?= /tmp/pycache
RETENTION_DAYS ?= 30

.PHONY: run demo ops ops-topology ops-alert-summary ops-audit-summary ops-audit-export ops-audit-retention-dry-run ops-audit-retention-apply test compile ci

run:
	$(PYTHON) scripts/dev_runner.py --reset-data

demo:
	$(PYTHON) scripts/demo_flow.py

ops:
	$(PYTHON) scripts/ops_report.py

ops-topology:
	$(PYTHON) scripts/ops_report.py topology

ops-alert-summary:
	$(PYTHON) scripts/ops_report.py alert-summary

ops-audit-summary:
	$(PYTHON) scripts/ops_report.py audit-summary

ops-audit-export:
	$(PYTHON) scripts/ops_report.py audit-export

ops-audit-retention-dry-run:
	$(PYTHON) scripts/ops_report.py audit-retention-dry-run $(RETENTION_DAYS)

ops-audit-retention-apply:
	$(PYTHON) scripts/ops_report.py audit-retention-apply $(RETENTION_DAYS)

test:
	PYTHONPYCACHEPREFIX=$(PYCACHE_PREFIX) $(PYTHON) -m unittest tests.test_end_to_end -v

compile:
	PYTHONPYCACHEPREFIX=$(PYCACHE_PREFIX) $(PYTHON) -m compileall services shared scripts tests

ci: compile test
