.DEFAULT_GOAL := help
SHELL := /bin/bash
PY := backend/.venv/Scripts/python.exe
ifeq ($(wildcard $(PY)),)
PY := backend/.venv/bin/python
endif

.PHONY: help bootstrap up down logs db-apply db-reset smoke migrate superuser \
        run worker beat test lint fmt check compliance schema-doc clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

bootstrap: ## One-command setup: containers, schema, venv, deps, migrations
	docker compose up -d
	@echo "Waiting for postgres..."
	@until docker compose exec -T db pg_isready -U agricrm -d agricrm >/dev/null 2>&1; \
	  do printf '.'; sleep 1; done; echo " ready"
	./scripts/db-apply.sh
	python -m venv backend/.venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r backend/requirements/dev.txt
	@test -f .env || cp .env.example .env
	cd backend && ../$(PY) manage.py migrate
	./scripts/smoke-test.sh
	@echo ""
	@echo "Ready. 'make run' to start the API, 'make superuser' for admin access."

up: ## Start postgres + redis
	docker compose up -d

down: ## Stop containers (data survives in the volume)
	docker compose down

logs: ## Tail container logs
	docker compose logs -f

db-apply: ## Apply schema.sql + seed_reference.sql
	./scripts/db-apply.sh

db-reset: ## 🔴 DROP every business schema and re-apply. Dev only.
	./scripts/db-reset.sh

smoke: ## Run the 15-assertion behavioural suite
	./scripts/smoke-test.sh

migrate: ## Apply Django migrations
	cd backend && ../$(PY) manage.py migrate

superuser: ## Create an admin user
	cd backend && ../$(PY) manage.py createsuperuser

run: ## Start the API on :8000 (Django Admin at /admin/)
	cd backend && ../$(PY) manage.py runserver 0.0.0.0:8000

worker: ## Start a Celery worker
	cd backend && ../$(PY) -m celery -A config worker -l info -Q default,import,heavy,messaging

beat: ## Start Celery Beat — 🔴 exactly one instance, ever
	cd backend && ../$(PY) -m celery -A config beat -l info

test: ## Run the backend test suite
	cd backend && ../$(PY) -m pytest -q

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
	cd backend && ../$(PY) manage.py spectacular --file ../openapi.yaml
	@echo "Wrote openapi.yaml"

clean: ## Remove caches and build artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache backend/.coverage
