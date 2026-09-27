"""Anomizer — модуль обезличивания документов.

Импорт защищён: если на сервере не установлены pymorphy2 / natasha, основное
приложение должно всё равно стартовать, а обезличивание — отдавать 503 с
текстом «что установить», а не ронять весь сервис.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Причина неудачного импорта, если она была (None — модуль доступен).
IMPORT_ERROR: BaseException | None = None

try:
    from .web_api import router
except ImportError as exc:  # нет pymorphy2 / natasha / python-docx
    IMPORT_ERROR = exc
    logger.warning(
        "Модуль Секретарь (обезличивание) недоступен: %s: %s. "
        "Установите: pip install pymorphy2 pymorphy2-dicts-ru natasha",
        exc.__class__.__name__,
        exc,
    )
    from .fallback import router

__all__ = ["router", "IMPORT_ERROR"]
