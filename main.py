"""Entry point: build the bot, load cogs from cogs/, and run it."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

import cogs
from config import Config, load_config
from services.ai_client import AIClient
from services.database import Database

logger = logging.getLogger("dsebot")


class DseBot(commands.Bot):
    """Discord bot with shared AI and database services attached."""

    def __init__(self, db: Database, ai: AIClient) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # required to read typed quiz answers
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )
        self.db = db
        self.ai = ai
        self._commands_synced = False

    async def setup_hook(self) -> None:
        await cogs.load_all(self)

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID %s)", self.user, self.user.id)
        if not self._commands_synced:
            await self.tree.sync()
            self._commands_synced = True
            logger.info("Synced %d slash command(s)", len(self.tree.get_commands()))


async def run_bot(config: Config) -> None:
    db = Database(config.db_path)
    await db.init()
    ai = AIClient(config.openrouter_api_key)
    bot = DseBot(db, ai)
    try:
        await bot.start(config.discord_token)
    finally:
        await bot.close()
        await ai.close()
        await db.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config()
    except RuntimeError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    try:
        asyncio.run(run_bot(config))
    except KeyboardInterrupt:
        logger.info("Shutting down")


if __name__ == "__main__":
    main()
