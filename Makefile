.DEFAULT_GOAL := help
.PHONY: help setup install sync lock requirements format lint typecheck test test-integration test-browser cov check build clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Use web-router: every extra (library, MCP server, browser tools) and a local Chromium
	uv sync --all-extras
	uv run playwright install chromium

install: ## Contribute to web-router: setup, plus the dev tools and pre-commit hooks
	uv sync --all-extras --dev
	uv run pre-commit install

sync: ## Install dependencies from the lockfile
	uv sync --all-extras --dev

lock: ## Refresh uv.lock
	uv lock

requirements: ## Regenerate the pip-compatible requirements files from uv.lock
	uv export --no-hashes --no-emit-project --all-extras --no-dev -o requirements.txt
	uv export --no-hashes --no-emit-project --all-extras --only-dev -o requirements-dev.txt

format: ## Auto-format and auto-fix
	uv run ruff format .
	uv run ruff check --fix .

lint: ## Lint without fixing
	uv run ruff check .
	uv run ruff format --check .

typecheck: ## Run mypy
	uv run mypy

test: ## Run the unit tests (no network, no browser)
	uv run pytest -m "not integration and not browser"

test-integration: ## Run tests that hit real provider APIs (needs credentials)
	uv run pytest -m integration

test-browser: ## Run tests that launch a real local browser (skip when none is installed)
	uv run pytest -m browser

cov: ## Unit tests with a coverage report
	uv run pytest -m "not integration and not browser" --cov --cov-report=term-missing

check: lint typecheck test ## The lint, typecheck and unit-test portion of CI (CI also checks hygiene and the build)

build: ## Build the sdist and wheel
	uv build

clean: ## Remove build and cache artifacts
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
