# Instagraph — see spec.md §8.7
BACKEND  := backend
FRONTEND := frontend
VENV     := $(BACKEND)/.venv
PY       := $(VENV)/bin/python
HOST     ?= 127.0.0.1
PORT     ?= 8000

.PHONY: setup dev dev-backend dev-frontend build run test e2e lint format gen-types

setup:
	test -d $(VENV) || python3 -m venv $(VENV)
	$(PY) -m pip install -e "$(BACKEND)[dev]"
	cd $(FRONTEND) && npm install

# Backend + frontend with hot reload (Ctrl+C stops both).
dev:
	$(MAKE) -j2 dev-backend dev-frontend

dev-backend:
	cd $(BACKEND) && .venv/bin/uvicorn instagraph.main:app --reload --host $(HOST) --port $(PORT)

dev-frontend:
	cd $(FRONTEND) && npm run dev

build:
	cd $(FRONTEND) && npm run build

run:
	cd $(BACKEND) && .venv/bin/uvicorn instagraph.main:app --host $(HOST) --port $(PORT)

test:
	cd $(BACKEND) && .venv/bin/pytest
	cd $(FRONTEND) && npm test

# Browser end-to-end test (§13): builds the frontend, starts the backend on 127.0.0.1 with a
# temporary data dir, imports a generated sample export through the UI. Needs Chromium
# (`cd frontend && npx playwright install chromium`), so it is not part of `make test`.
e2e:
	cd $(FRONTEND) && npm run e2e

lint:
	cd $(BACKEND) && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy --strict src
	cd $(FRONTEND) && npm run lint && npm run typecheck

format:
	cd $(BACKEND) && .venv/bin/ruff check --fix . && .venv/bin/ruff format .

# Regenerate frontend/src/api/schema.d.ts from FastAPI's app.openapi().
gen-types:
	cd $(BACKEND) && .venv/bin/python scripts/dump_openapi.py openapi.json
	cd $(FRONTEND) && npx openapi-typescript ../$(BACKEND)/openapi.json -o src/api/schema.d.ts
