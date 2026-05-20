.PHONY: help install dev web bot build test clean

# ── Default ───────────────────────────────────────────────────────────────────
help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  install   Install all Python + Node dependencies"
	@echo "  dev       Start backend (port 8000) + frontend dev server (port 5173)"
	@echo "  web       Start backend API server only"
	@echo "  bot       Start the Discord bot only"
	@echo "  build     Build the React frontend into frontend/dist/"
	@echo "  test      Run Python tests"
	@echo "  clean     Remove build artefacts and __pycache__"

# ── Dependencies ──────────────────────────────────────────────────────────────
install:
	pip install -r requirements.txt
	cd frontend && npm install

# ── Development ───────────────────────────────────────────────────────────────
dev:
	@echo "Starting backend on :8000 and frontend dev server on :5173 ..."
	@trap 'kill 0' INT; \
	python -m uvicorn web.app:app --reload --port 8000 & \
	(cd frontend && npm run dev) & \
	wait

web:
	python -m uvicorn web.app:app --reload --port 8000

bot:
	python main.py

# ── Build ─────────────────────────────────────────────────────────────────────
build:
	cd frontend && npm run build

# ── Testing ───────────────────────────────────────────────────────────────────
test:
	pytest

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	cd frontend && rm -rf dist
	find . -type d -name __pycache__ -not -path './.git/*' -exec rm -rf {} +
	find . -name '*.pyc' -not -path './.git/*' -delete
