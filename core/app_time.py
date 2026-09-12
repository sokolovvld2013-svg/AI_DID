"""Время приложения для истории и протоколов (по умолчанию — Москва)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import APP_TIMEZONE

_HISTORY_FMT = "%d.%m.%Y %H:%M"

def app_timezone():
    """Часовой пояс приложения с резервным вариантом UTC+3.

    На Windows в Python 3.9+ системной базы таймзон нет — подключаем
    пакет tzdata, а если и его нет — фиксированный сдвиг UTC+3.
    """
    try:
        return ZoneInfo(APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        try:
            import tzdata  # noqa: F401  регистрирует базу таймзон для zoneinfo

            return ZoneInfo(APP_TIMEZONE)
        except (ImportError, ZoneInfoNotFoundError):
            return timezone(timedelta(hours=3), "UTC+3")


def now_app() -> datetime:
    return datetime.now(app_timezone())


def format_history_timestamp(dt: datetime | None = None) -> str:
    """Метка времени для истории действий (человекочитаемый формат)."""
    return (dt or now_app()).strftime(_HISTORY_FMT)
