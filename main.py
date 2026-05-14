import asyncio
import os

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")

    from bot.bot import EQ2Bot

    bot = EQ2Bot()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
