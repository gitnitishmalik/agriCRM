.DEFAULT_GOAL := help
SHELL := /bin/bash
PY := .venv/Scripts/python.exe
ifeq ($(wildcard $(PY)),)
PY := .venv/bin/python
endif

# 🔴 Python 3.13. `python` on PATH is whatever the machine happens to default
# to, and pyproject requires >=3.13 — a venv built on 3.12 installs and then
# fails at import. Overridable: `make bootstrap PY_BOOTSTRAP="py -3.13"`.
PY_BOOTSTRAP ?= python

.PHONY: help bootstrap up down logs db-apply db-migrate db-reset smoke migrate superuser \
        run dev frontend frontend-install collector test test-frontend lint fmt check \
        compliance schema-doc doctor clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

bootstrap: ## One-command setup: containers, venv, deps, schema, frontend
	@echo "==> Containers"
	docker compose up -d
	@echo "Waiting for postgres..."
	@until docker compose exec -T db pg_isready -U agricrm -d agricrm >/dev/null 2>&1; \
	  do printf '.'; sleep 1; done; echo " ready"
	@# 🔴 .env first. scripts/_lib.sh resolves the target database from it, so
	@# creating it afterwards meant the schema could be applied to a different
	@# database than the one the application then talks to.
	@test -f .env || { cp .env.example .env; echo "==> Wrote .env from .env.example"; }
	@# 🔴 The venv before the schema, not after. scripts/db-apply.sh falls back
	@# to scripts/pgrun.py when psql is not on PATH — the normal case on
	@# Windows — and pgrun needs psycopg from this venv. Applying the schema
	@# first meant that fallback could not exist yet.
	@echo "==> Python environment"
	$(PY_BOOTSTRAP) -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r backend/requirements.txt -r backend/requirements-dev.txt
	@echo "==> Schema"
	./scripts/db-apply.sh
	./scripts/smoke-test.sh
	$(PY) -m backend.cli seed-billing-entities
	@$(MAKE) --no-print-directory frontend-install
	@echo ""
	@echo "Ready."
	@echo "  make dev             API on :8001 and the UI on :5173, together"
	@echo "  make run             API only; console at http://localhost:8001/admin"
	@echo "  make seed-dev-users  development logins (refuses unless DEBUG is on)"
	@echo "  make superuser       create a real admin account"
	@echo "  make doctor          check the environment if something looks wrong"

frontend-install: ## Install frontend dependencies
	@if command -v npm >/dev/null 2>&1; then \
	  echo "==> Frontend dependencies"; \
	  cd frontend && npm install --silent; \
	else \
	  echo "!! npm not found — skipping the frontend."; \
	  echo "   Install Node 22+, then run 'make frontend-install'."; \
	fi

up: ## Start postgres + redis
	docker compose up -d

down: ## Stop containers (data survives in the volume)
	docker compose down

logs: ## Tail container logs
	docker compose logs -f

db-apply: ## Apply schema.sql + additions + seed_reference.sql (empty database)
	./scripts/db-apply.sh

db-migrate: ## Apply only the idempotent additions (safe on a live database)
	./scripts/db-migrate.sh

db-reset: ## 🔴 DROP every business schema and re-apply. Dev only.
	./scripts/db-reset.sh --yes

smoke: ## Run the 20-assertion behavioural suite
	./scripts/smoke-test.sh

migrate: ## Apply reviewed SQL additions (no ORM migrations)
	./scripts/db-migrate.sh

superuser: ## Create an admin user
	$(PY) -m backend.cli create-admin --email "$$ADMIN_EMAIL" --name "$$ADMIN_NAME"

seed-entities: ## Create or refresh TFD and TEPL from the real invoices
	$(PY) -m backend.cli seed-billing-entities

seed-dev-users: ## Development roster (refuses unless DEBUG is on)
	$(PY) -m backend.cli seed-dev-users

run: ## Start FastAPI on :8001
	$(PY) -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8001

frontend: ## Start the Vite dev server on :5173 (proxies /api to :8001)
	cd frontend && npm run dev

dev: ## Run the API and the UI together; Ctrl-C stops both
	@echo "API  http://localhost:8001  (docs /api/docs · console /admin)"
	@echo "UI   http://localhost:5173"
	@echo ""
	@# The trap is what makes one Ctrl-C enough. Without it a server survives in
	@# the background holding its port, and the next 'make dev' fails to bind
	@# with an error that does not mention the previous run.
	@#
	@# 🔴 PIDs explicitly, not `kill 0`. On Git Bash the npm wrapper and the
	@# node process it spawns are not reliably in this shell's process group,
	@# so a group kill takes the API down and leaves Vite holding :5173 —
	@# measured. Killing the recorded PIDs, then anything still on the ports,
	@# covers both. `|| true` throughout: this runs while shutting down, and a
	@# failure to kill something already dead must not mask the real exit.
	@trap 'kill $$API $$UI 2>/dev/null || true; \
	       sleep 1; \
	       for port in 8001 5173; do \
	         pid=$$(netstat -ano 2>/dev/null | grep -E "LISTENING" | grep ":$$port " \
	                | awk "{print \$$5}" | head -1); \
	         [ -n "$$pid" ] && taskkill //PID $$pid //F >/dev/null 2>&1; \
	       done; true' EXIT INT TERM; \
	  $(PY) -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8001 & API=$$!; \
	  (cd frontend && npm run dev) & UI=$$!; \
	  wait

doctor: ## Check the environment and report what is missing
	@$(PY) scripts/doctor.py

collector: ## Run the approved SFAC collector (ARGS="--dry-run --limit 5")
	$(PY) -m backend.collectors.run sfac $(ARGS)

test: ## Run the backend test suite
	$(PY) -m pytest -q -c backend/pytest.ini backend/tests

test-frontend: ## Run the frontend typecheck and unit tests
	cd frontend && npm run typecheck && npm test

lint: ## Lint and format-check
	$(PY) -m ruff check backend/
	$(PY) -m ruff format --check backend/

fmt: ## Auto-format
	$(PY) -m ruff check --fix backend/
	$(PY) -m ruff format backend/

compliance: ## Run the compliance guards CI enforces
	./scripts/check-r6.sh

check: lint compliance test smoke ## Everything CI runs, locally

schema-doc: ## Regenerate the OpenAPI schema
	$(PY) -c "import yaml; from backend.main import app; open('openapi.yaml','w',encoding='utf-8').write(yaml.safe_dump(app.openapi(),sort_keys=False))"
	@echo "Wrote openapi.yaml"

clean: ## Remove caches and build artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .coverage
