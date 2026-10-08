.DEFAULT_GOAL := check
PYTHON ?= python3

check:
	@$(PYTHON) tests/test_run_bounded_review.py
	@$(PYTHON) tests/test_integrations.py

record:
	@$(PYTHON) scripts/record_session.py

demo:
	@$(PYTHON) scripts/generate_demo.py

assets:
	@$(PYTHON) assets/build.py

asset-check:
	@$(PYTHON) assets/build.py --check

.PHONY: check record demo assets asset-check
