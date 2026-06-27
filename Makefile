.PHONY: help install install-prod dev web bot build test check clean

# ── Default ───────────────────────────────────────────────────────────────────
help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  install        Install all Python + Node dependencies (prod + dev)"
	@echo "  install-prod   Install prod Python deps only (mirrors Railway/nixpacks)"
	@echo "  dev            Start backend (port 8000) + frontend dev server (port 5173)"
	@echo "  web            Start backend API server only"
	@echo "  bot            Start the Discord bot only"
	@echo "  build          Build the React frontend into frontend/dist/"
	@echo "  test           Run Python tests"
	@echo "  check          Run the full pre-push pipeline (tsc, ruff, pyright, pytest)"
	@echo "  clean          Remove build artefacts and __pycache__"

# ── Dependencies ──────────────────────────────────────────────────────────────
install:
	uv sync --all-groups
	cd frontend && npm install

install-prod:
	uv sync --frozen --no-dev

# ── Development ───────────────────────────────────────────────────────────────
dev:
	@echo "Starting backend on :8000 and frontend dev server on :5173 ..."
	@trap 'kill 0' INT; \
	uv run uvicorn backend.server.app:app --reload --reload-dir backend --port 8000 & \
	(cd frontend && npm run dev) & \
	wait

web:
	uv run uvicorn backend.server.app:app --reload --reload-dir backend --port 8000

bot:
	uv run python main.py

# ── Build ─────────────────────────────────────────────────────────────────────
build:
	cd frontend && npm run build

# ── Testing ───────────────────────────────────────────────────────────────────
test:
	uv run pytest

# ── Full pre-push pipeline ────────────────────────────────────────────────────
# Mirrors .githooks/pre-push so you can run the same checks without pushing.
# Stops on the first failure (Make default).
check:
	cd frontend && ./node_modules/.bin/tsc -b
	uv run --frozen ruff format --check .
	uv run --frozen ruff check .
	uv run --frozen pyright
	uv run --frozen pytest --tb=short -q

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	cd frontend && rm -rf dist
	find . -type d -name __pycache__ -not -path './.git/*' -exec rm -rf {} +
	find . -name '*.pyc' -not -path './.git/*' -delete
