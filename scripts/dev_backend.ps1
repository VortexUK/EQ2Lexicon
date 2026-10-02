# --reload-dir backend: watch only the code dir so writes to data/ (SQLite WAL
#   files, downloaded icons, etc.) don't churn the watcher. Everything (server,
#   census, eq2db, parses, bot) lives under backend/ since the #46 refactor.
# --timeout-graceful-shutdown 2: the browser holds an open SSE stream
#   (/api/backend/census/stream) proxied through Vite; a graceful reload waits
#   for open connections to drain and an infinite stream never does — without
#   this, every reload hangs until the frontend is killed. (BE-045's lifespan
#   manager cancels background TASKS; an in-flight SSE request isn't one.)
# Push/Pop instead of Set-Location so Ctrl+C doesn't move the caller's
# shell (the finally runs even when the pipeline is stopped).
try {
    Push-Location E:\git\EQ2Lexicon
    uv run uvicorn backend.server.app:app --port 8000 --reload --reload-dir backend --timeout-graceful-shutdown 2
} finally {
    Pop-Location
}
