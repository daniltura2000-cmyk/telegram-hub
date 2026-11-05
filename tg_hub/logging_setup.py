"""Налаштування логування: консоль + ротаційний файл logs/hub.log."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = "hub.log"
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(level: str = "INFO", logs_dir: Path | None = None) -> None:
    """Налаштувати кореневий логер один раз (повторні виклики ігноруються)."""
    root = logging.getLogger()
    if getattr(root, "_tg_hub_configured", False):
        return

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(numeric_level)
    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if logs_dir is not None:
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                logs_dir / LOG_FILE_NAME,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:  # немає прав на запис — працюємо лише з консоллю
            root.warning("Не вдалося створити файл логів у %s", logs_dir)

    root._tg_hub_configured = True  # type: ignore[attr-defined]
