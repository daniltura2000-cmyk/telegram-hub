"""Точка входу TelegramHub: Telethon userbot + aiogram контрольний бот в одному циклі."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .config import Config, load_config
from .control_bot import build_router
from .db import Database
from .logging_setup import setup_logging
from .reminders import reminder_loop
from .userbot import Userbot

logger = logging.getLogger(__name__)


def _make_admin_notifier(bot: Bot, admin_ids: tuple[int, ...]):
    """Повертає async callback(text): надсилає коротке повідомлення першому адміну."""
    async def notify(text: str) -> None:
        if not admin_ids:
            return
        try:
            await bot.send_message(admin_ids[0], text)
        except Exception:  # noqa: BLE001 — сповіщення не повинно ламати потік
            logger.exception("Не вдалося надіслати сповіщення адміну")

    return notify


def _make_reminder_sender(bot: Bot):
    """Повертає async callback(user_id, text) для фонового циклу нагадувань."""
    async def send(user_id: int, text: str) -> None:
        try:
            await bot.send_message(user_id, text)
        except Exception:  # noqa: BLE001 — нагадування не повинно ламати цикл
            logger.exception("Не вдалося надіслати нагадування користувачу %s", user_id)

    return send


async def run() -> None:
    config: Config = load_config()
    setup_logging(config.log_level, config.logs_dir)
    logger.info("Запуск TelegramHub…")

    db = Database(config.database_path)
    await db.init()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    userbot = Userbot(config, db)
    userbot.set_error_notifier(_make_admin_notifier(bot, config.admin_ids))
    router = build_router(config, db, userbot)
    dp.include_router(router)

    # Фоновий цикл нагадувань (перевірка кожні 30 с)
    reminders_task = asyncio.create_task(reminder_loop(db, _make_reminder_sender(bot)))

    if await userbot.start():
        logger.info("Userbot активний — авто-відповіді можуть працювати")
    else:
        logger.warning(
            "Userbot не запущено: підключіть акаунт через контрольний бот "
            "або задайте TELEGRAM_API_ID/TELEGRAM_API_HASH"
        )

    # Коректна зупинка за SIGINT/SIGTERM (на Windows — лише SIGINT).
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop() -> None:
        logger.info("Отримано сигнал зупинки…")
        stop_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows: add_signal_handler не підтримується — покладаємось на KeyboardInterrupt
            pass

    polling_task = asyncio.create_task(dp.start_polling(bot))
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, pending = await asyncio.wait(
            {polling_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    finally:
        # Очищення ГАРАНТОВАНО виконується і при зовнішній отмені run()
        # (Windows: KeyboardInterrupt перетворюється asyncio.run на CancelledError),
        # і при штатному сигналі зупинки.
        for task in (stop_task, polling_task, reminders_task):
            task.cancel()
        for task, name in ((polling_task, "polling"), (reminders_task, "reminders"),
                           (stop_task, "stop")):
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — зупинка не повинна падати
                logger.exception("Помилка зупинки задачі %s", name)
        # нагадування вже зупинено вище — тепер можна безпечно закривати клієнтів
        await userbot.stop()
        control_bot = getattr(router, "control_bot", None)
        if control_bot is not None:
            # незавершені підключення/QR-очікування не мають пережити зупинку (docs/TZ_v2.md B2)
            await control_bot.close_pending()
        await bot.session.close()
        logger.info("TelegramHub зупинено")


def main() -> None:
    """Синхронна точка входу (console script / python -m tg_hub.main)."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:  # некоректна конфігурація (.env)
        print(f"Помилка конфігурації: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    sys.exit(main())
