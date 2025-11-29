.PHONY: test test-unit test-integration test-e2e coverage

export PYTHONPATH=src
PYTEST=.venv/bin/pytest

test:
	$(PYTEST)

test-unit:
	$(PYTEST) tests/unit

test-integration:
	$(PYTEST) tests/integration

test-e2e:
	$(PYTEST) tests/e2e

coverage:
	$(PYTEST) --cov=src/code_graph_indexer --cov-report=html
