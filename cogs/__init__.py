"""Cog package: discovers and loads every cog module in this folder."""

from __future__ import annotations

import importlib
import logging
import pkgutil

from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("dsebot.cogs")


async def load_all(bot: commands.Bot) -> None:
    """Import every cogs/<name>.py module and add its first Cog subclass."""
    for module_info in pkgutil.iter_modules(__path__):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        cog_class = next(
            (
                value
                for value in vars(module).values()
                if isinstance(value, type)
                and issubclass(value, commands.Cog)
                and value is not commands.Cog
            ),
            None,
        )
        if cog_class is None:
            logger.warning("No Cog subclass found in %s; skipped.", module.__name__)
            continue
        await bot.add_cog(cog_class(bot))
        logger.info("Loaded cog %s", cog_class.__name__)
        for value in vars(module).values():
            if isinstance(
                value, app_commands.ContextMenu
            ) and value not in bot.tree.get_commands():
                bot.tree.add_command(value)
                logger.info("Loaded context menu %s", value.name)
