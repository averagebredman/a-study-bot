"""Environment configuration loaded from a local .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_DB_PATH = "data/bot.db"


@dataclass(frozen=True)
class Config:
    discord_token: str
    openrouter_api_key: str
    db_path: str


def load_config() -> Config:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN", "").strip()
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    db_path = os.getenv("DB_PATH", "").strip() or DEFAULT_DB_PATH
    missing = [
        name
        for name, value in (("DISCORD_TOKEN", token), ("OPENROUTER_API_KEY", api_key))
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Copy .env.example to .env and fill in the values."
        )
    return Config(discord_token=token, openrouter_api_key=api_key, db_path=db_path)
