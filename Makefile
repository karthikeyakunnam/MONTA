.PHONY: help dev stop backend frontend worker db-up db-migrate setup clean test

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ========================
# Setup
# ========================

setup: ## Initial project setup
	cp -n .env.example .env || true
	cd frontend/web && npm install
	cd backend && pip install -r requirements.txt
	cd workers && pip install -r requirements.txt
	docker-compose up -d postgres redis qdrant
	@echo "✅ MONTA setup complete"

# ========================
# Development
# ========================

dev: ## Start all services for development
	docker-compose up -d postgres redis qdrant
	@echo "Starting backend..."
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
	@echo "Starting worker..."
	cd workers && celery -A celery_app worker --loglevel=info &
	@echo "Starting frontend..."
	cd frontend/web && npm run dev &
	@echo "✅ All services running"

backend: ## Start FastAPI backend
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend: ## Start Next.js frontend
	cd frontend/web && npm run dev

worker: ## Start Celery worker
	cd workers && celery -A celery_app worker --loglevel=info

# ========================
# Database
# ========================

db-up: ## Start database services
	docker-compose up -d postgres redis qdrant

db-migrate: ## Run database migrations
	cd backend && alembic upgrade head

db-revision: ## Create new migration
	cd backend && alembic revision --autogenerate -m "$(MSG)"

# ========================
# Tests
# ========================

test: ## Run the Layer 3-7 test suite
	python -m pytest

# ========================
# Docker
# ========================

docker-up: ## Start all Docker services
	docker-compose up -d

docker-down: ## Stop all Docker services
	docker-compose down

docker-build: ## Build all Docker images
	docker-compose build

docker-logs: ## View Docker logs
	docker-compose logs -f

# ========================
# Cleanup
# ========================

clean: ## Clean build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .next -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ Cleaned"

stop: ## Stop all running services
	docker-compose down
	@pkill -f "uvicorn" || true
	@pkill -f "celery" || true
	@echo "✅ All services stopped"
