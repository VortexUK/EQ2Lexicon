Set-Location E:\git\EQ2Lexicon
# --reload-dir backend: watch only the code dir so writes to data/ (SQLite WAL
#   files, downloaded icons, etc.) don't churn the watcher. Everything (server,
#   census, eq2db, parses, bot) lives under backend/ since the #46 refactor.
# BE-045: --timeout-graceful-shutdown 2 removed — Phase 2a.13's lifespan
#   context manager now tracks and cancels all background tasks cleanly.
uv run uvicorn backend.server.app:app --port 8000 --reload --reload-dir backend
