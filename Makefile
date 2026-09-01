.DEFAULT_GOAL := help
SHELL := /bin/bash
PY := .venv/Scripts/python.exe
ifeq ($(wildcard $(PY)),)
PY := .venv/bin/python
endif

.PHONY: help bootstrap up down logs db-apply db-migrate db-reset smoke migrate superuser \
        run collector test lint fmt check compliance schema-doc clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

bootstrap: ## One-command setup: containers, schema, venv, deps, migrations
	docker compose up -d
	@echo "Waiting for postgres..."
	@until docker compose exec -T db pg_isready -U agricrm -d agricrm >/dev/null 2>&1; \
	  do printf '.'; sleep 1; done; echo " ready"
	./scripts/db-apply.sh
	python -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r backend/requirements.txt -r backend/requirements-dev.txt
	@test -f .env || cp .env.example .env
	./scripts/smoke-test.sh
	$(PY) -m backend.cli seed-billing-entities
	@echo ""
	@echo "Ready. 'make run' starts the API on :8001; the console is at /admin."
	@echo "'make superuser' creates an account, or 'make seed-dev-users' in dev."

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

collector: ## Run the approved SFAC collector (ARGS="--dry-run --limit 5")
	$(PY) -m backend.collectors.run sfac $(ARGS)

test: ## Run the backend test suite
	$(PY) -m pytest -q -c backend/pytest.ini backend/tests

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
