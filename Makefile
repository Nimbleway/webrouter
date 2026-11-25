.DEFAULT_GOAL := help
.PHONY: help setup install sync lock requirements format lint typecheck test test-integration test-browser cov contracts oxylabs-classic prices vendors docs-site docs-preview latency chains efforts data-driven-router changelog shape-baseline check build clean

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

contracts: ## Regenerate the committed contracts; PROVIDER=search/brave starts a new one
	uv run python -m scripts.contracts $(if $(PROVIDER),--new $(PROVIDER))

vendors: ## Regenerate VENDORS.md and the vendor section of .env.example from src/web_router/data/vendors
	uv run python -m scripts.vendors

docs-site: ## Build the docs site into build/docs-site/docs: docs/ plus the Data APIs pages generated from the catalog
	uv run python -m scripts.data_api_docs

docs-preview: docs-site ## Build the docs site and serve it locally with Mintlify
	cd build/docs-site/docs && npx --yes mint@4.3.49 dev

oxylabs-classic: ## Regenerate src/web_router/data/oxylabs_classic_sources.json from Oxylabs' docs (network)
	uv run python -m scripts.oxylabs_classic

prices: ## Regenerate PRICING.md's price tables from src/web_router/data/prices.json
	uv run python -m scripts.prices

latency: ## Rewrite prices.json's latency fields from the frozen benchmark results
	uv run python -m scripts.latency

chains: ## Regenerate src/web_router/data/chains.json, the default auto orders, from the frozen results
	uv run --all-extras python benchmarks/providers/chains.py

efforts: ## Regenerate src/web_router/data/efforts.json, the research effort table, from the frozen results
	uv run --all-extras python benchmarks/providers/efforts.py

data-driven-router: ## Import a data-driven router release: SOURCE=<delivered folder> VERSION=0.2.0 [LOOKUP=<lookup table>]
	uv run --all-extras python -m scripts.data_driven_router --source $(SOURCE) --version $(VERSION) $(if $(LOOKUP),--lookup $(LOOKUP))

changelog: ## Move the changelog.d/ fragments under [Unreleased] in CHANGELOG.md (at release)
	uv run python -m scripts.changelog

shape-baseline: ## Rewrite tests/shape_baseline.json after a grandfathered file shrank; it refuses new or grown entries
	uv run python -m scripts.shape

cov: ## Unit tests with a coverage report
	uv run pytest -m "not integration and not browser" --cov --cov-report=term-missing

check: lint typecheck test ## The lint, typecheck and unit-test portion of CI (CI also checks hygiene and the build)

build: ## Build the sdist and wheel
	uv build

clean: ## Remove build and cache artifacts
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
