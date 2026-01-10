.PHONY: test test-unit test-integration test-e2e coverage

export PYTHONPATH=src
PYTEST=.venv/bin/python -m pytest

test:
	$(PYTEST)

test-unit:
	$(PYTEST) tests/unit

test-integration:
	$(PYTEST) tests/integration

test-e2e:
	$(PYTEST) tests/e2e

coverage:
	$(PYTEST) tests/unit tests/integration --cov=src/crader --cov-report=html
