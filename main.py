import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from dotenv import load_dotenv

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
# Configure once before anything else imports logging.  force=True semantics
# are inside configure_logging() — re-applies even if uvicorn already touched
# the root logger.  Reads LOG_LEVEL + LOG_FORMAT from env.
from backend.core.logging_config import configure_logging  # noqa: E402

configure_logging()


async def run_bot() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    from backend.bot.bot import EQ2Bot

    bot = EQ2Bot()
    async with bot:
        await bot.start(token)


async def run_web() -> None:
    import uvicorn

    from backend.server.app import app

    port = int(os.getenv("PORT", "8000"))
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        # Disable reload in production; enable locally via WEB_RELOAD=1
        reload=os.getenv("WEB_RELOAD", "0") == "1",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def _supervise(
    name: str,
    factory: Callable[[], Awaitable[None]],
    max_restarts: int = 10,
) -> None:
    """Run `factory()`; on unexpected crash, log + back off + restart so one
    side's bug doesn't take the other down. Gives up after max_restarts
    consecutive failures rather than spinning forever on a config error."""
    log = logging.getLogger(f"supervisor.{name}")
    delay = 2.0
    restarts = 0
    while True:
        try:
            log.info("starting")
            await factory()
            log.info("exited cleanly")
            return
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            restarts += 1
            if restarts > max_restarts:
                log.exception("crashed %d times in a row — giving up", restarts - 1)
                return
            log.exception("crashed; restart %d/%d in %.1fs", restarts, max_restarts, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)


async def main() -> None:
    await asyncio.gather(
        _supervise("bot", run_bot),
        _supervise("web", run_web),
    )


if __name__ == "__main__":
    asyncio.run(main())
