"""Время приложения для истории и протоколов (по умолчанию — Москва)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from config import APP_TIMEZONE

_HISTORY_FMT = "%d.%m.%Y %H:%M"


def app_timezone() -> ZoneInfo:
    return ZoneInfo(APP_TIMEZONE)


def now_app() -> datetime:
    return datetime.now(app_timezone())


def format_history_timestamp(dt: datetime | None = None) -> str:
    """Метка времени для истории действий (человекочитаемый формат)."""
    return (dt or now_app()).strftime(_HISTORY_FMT)
