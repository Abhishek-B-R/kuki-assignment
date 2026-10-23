# Run `make help` for targets. Requires uv (Python) and Node 20+ with npm (dashboard).
SHELL := /bin/bash
PY := cd backend && uv run
PKG := npm

.PHONY: help setup api worker dev test eval-baseline eval-improved eval-repeats compare build clean

help:
	@echo "setup          install backend + dashboard dependencies"
	@echo "dev            API on :8000 + worker + dashboard dev server on :5173"
	@echo "api / worker   run one of them"
	@echo "test           backend test suite"
	@echo "eval-baseline  ingest dataset/ with extraction v1 and run the eval suite"
	@echo "eval-improved  rerun the same cases with extraction v2 (re-extracts the workbook)"
	@echo "eval-repeats   rerun the answer step 4 more times on each pipeline's documents (noise check)"
	@echo "compare        write backend/evals/results/comparison.md"
	@echo "build          build the dashboard so the API serves it at :8000"

setup:
	cd backend && uv sync
	cd frontend && npm ci

api:
	$(PY) uvicorn app.api:create_app --factory --host 127.0.0.1 --port 8000

worker:
	$(PY) python -m app.worker

dev:
	@trap 'kill 0' EXIT; \
	(cd backend && uv run uvicorn app.api:create_app --factory --port 8000) & \
	(cd backend && uv run python -m app.worker) & \
	(cd frontend && $(PKG) run dev) & \
	wait

test:
	$(PY) python -m pytest -q

eval-baseline:
	$(PY) python -m evals.run --name baseline --version v1

eval-improved:
	$(PY) python -m evals.run --name improved --version v2 --reuse-db baseline --reextract "Annual Electricity Consumption.xlsx"

eval-repeats:
	cd backend && for r in 2 3 4 5; do \
	  uv run python -m evals.run --name baseline-r$$r --version v1 --reuse-db baseline && \
	  uv run python -m evals.run --name improved-r$$r --version v2 --reuse-db improved || exit 1; \
	done

compare:
	$(PY) python -m evals.compare

build:
	cd frontend && $(PKG) run build

clean:
	rm -rf data backend/evals/.work
