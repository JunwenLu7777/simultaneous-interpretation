.PHONY: lint typecheck test benchmark coverage

UV_RUN := uv run --extra dev

lint:
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .
	$(UV_RUN) radon cc -a -nb src tests

typecheck:
	$(UV_RUN) mypy --strict

test:
	$(UV_RUN) pytest tests/unit tests/contract tests/integration

benchmark:
	$(UV_RUN) pytest tests/perf --benchmark-only

coverage:
	$(UV_RUN) coverage run -m pytest
	$(UV_RUN) coverage report
