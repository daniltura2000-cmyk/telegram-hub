"""Конфігурація: змінні оточення та .env.

Модуль НІКОЛИ не читає .env під час імпорту — це робиться явно
через :func:`load_config`, яку викликає лише точка входу.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_DATABASE_PATH = "data/hub.db"
DEFAULT_LLM_TIMEOUT = 60.0
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_ANALYSIS_LIMIT = 500


@dataclass(frozen=True)
class Config:
    """Іммутабельна конфігурація застосунку."""

    bot_token: str
    admin_ids: tuple[int, ...]
    telegram_api_id: int | None
    telegram_api_hash: str | None
    database_path: Path
    llm_timeout: float
    log_level: str
    analysis_limit: int

    @property
    def sessions_dir(self) -> Path:
        return Path("sessions")

    @property
    def logs_dir(self) -> Path:
        return Path("logs")


def _parse_admin_ids(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return ()
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return tuple(ids)


def _parse_optional_int(raw: str | None) -> int | None:
    if not raw or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def load_config(env_file: str | os.PathLike[str] | None = ".env") -> Config:
    """Зчитати конфігурацію з оточення (і, за бажанням, з .env-файлу)."""
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)

    bot_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not bot_token:
        raise RuntimeError(
            "Змінна оточення TELEGRAM_BOT_TOKEN не задана. "
            "Скопіюйте .env.example у .env та заповніть токен бота."
        )

    api_id = _parse_optional_int(os.getenv("TELEGRAM_API_ID"))
    api_hash = (os.getenv("TELEGRAM_API_HASH") or "").strip() or None

    timeout_raw = (os.getenv("LLM_TIMEOUT") or "").strip()
    try:
        llm_timeout = float(timeout_raw) if timeout_raw else DEFAULT_LLM_TIMEOUT
    except ValueError:
        llm_timeout = DEFAULT_LLM_TIMEOUT

    analysis_raw = (os.getenv("ANALYSIS_LIMIT") or "").strip()
    try:
        analysis_limit = int(analysis_raw) if analysis_raw else DEFAULT_ANALYSIS_LIMIT
    except ValueError:
        analysis_limit = DEFAULT_ANALYSIS_LIMIT

    return Config(
        bot_token=bot_token,
        admin_ids=_parse_admin_ids(os.getenv("ADMIN_IDS")),
        telegram_api_id=api_id,
        telegram_api_hash=api_hash,
        database_path=Path(os.getenv("DATABASE_PATH") or DEFAULT_DATABASE_PATH),
        llm_timeout=llm_timeout,
        log_level=(os.getenv("LOG_LEVEL") or DEFAULT_LOG_LEVEL).upper(),
        analysis_limit=analysis_limit,
    )
