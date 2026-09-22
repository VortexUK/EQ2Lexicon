import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Awaitable, Callable

from dotenv import load_dotenv

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
# Configure once before anything else imports logging.  force=True semantics
# are inside configure_logging() — re-applies even if uvicorn already touched
# the root logger.  Reads LOG_LEVEL + LOG_FORMAT from env.
from backend.core.logging_config import configure_logging  # noqa: E402

configure_logging()


# Set on SIGTERM/SIGINT — the ONE coordinated stop signal for both halves.
# Railway deploys send SIGTERM and then wait for the container to exit; the
# volume can't attach to the new deployment until the old one is gone, so
# every second the old process lingers is a second of Cloudflare 52x. The
# web half used to stop on its own (uvicorn's handlers) while the bot ran
# on until the SIGKILL grace expired — the whole grace period, every deploy.
_shutdown: asyncio.Event = asyncio.Event()


async def run_bot() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    import discord

    from backend.bot.bot import EQ2Bot

    bot = EQ2Bot()
    try:
        async with bot:
            starter = asyncio.create_task(bot.start(token))
            stopper = asyncio.create_task(_shutdown.wait())
            done, _ = await asyncio.wait({starter, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if starter in done:
                stopper.cancel()
                starter.result()  # re-raise a crash to the supervisor
            else:
                # Shutdown requested: stop the gateway task; `async with bot`
                # closes the client (and its background tasks) on exit.
                starter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await starter
    except discord.PrivilegedIntentsRequired:
        # A missing dev-portal toggle must not take the web half down: log
        # loudly and exit this supervised task cleanly (no restart storm).
        logging.getLogger("supervisor.bot").critical(
            "SERVER MEMBERS INTENT is not enabled for this bot. Enable it at "
            "https://discord.com/developers/applications -> your app -> Bot -> "
            "Privileged Gateway Intents, then restart. The web server continues without the bot."
        )
    except discord.LoginFailure:
        # Bad token — restarting can't fix it; same clean-exit treatment.
        logging.getLogger("supervisor.bot").critical(
            "Discord rejected DISCORD_TOKEN (401). Get a fresh token from "
            "https://discord.com/developers/applications -> your app -> Bot -> Reset Token, "
            "update .env (and Railway if you reset it), then restart. "
            "The web server continues without the bot."
        )


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
        # Open SSE streams (census stream) never disconnect on their own —
        # without this cap uvicorn's graceful shutdown waits on them forever
        # and the old deployment holds the Railway volume until SIGKILL.
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)
    # main() owns process signals (one coordinated stop for web AND bot) —
    # uvicorn must not install its own handlers over ours.
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    serve = asyncio.create_task(server.serve())
    stopper = asyncio.create_task(_shutdown.wait())
    done, _ = await asyncio.wait({serve, stopper}, return_when=asyncio.FIRST_COMPLETED)
    if serve in done:
        stopper.cancel()
        serve.result()  # re-raise a crash to the supervisor
        return
    server.should_exit = True
    await serve


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
    # One handler for both halves: SIGTERM (Railway deploys/stops) and
    # SIGINT set the shared shutdown event; run_web tells uvicorn to exit
    # (5s graceful cap) and run_bot closes the Discord client, so the
    # process is gone in seconds instead of hanging until SIGKILL with the
    # Railway volume still attached.
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _shutdown.set)
        except (NotImplementedError, RuntimeError):
            # Windows event loops can't add async signal handlers — fall
            # back to a classic handler that trampolines into the loop.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(_shutdown.set))

    await asyncio.gather(
        _supervise("bot", run_bot),
        _supervise("web", run_web),
    )


if __name__ == "__main__":
    asyncio.run(main())
