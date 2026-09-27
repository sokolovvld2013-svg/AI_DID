"""Заглушка маршрутов обезличивания.

Подключается вместо `web_api`, если импорт пайплайна не удался (не установлены
pymorphy2 / natasha). Зависит только от FastAPI, поэтому не тянет за собой
тяжёлые пакеты. Сохраняет контракт эндпоинта `POST /api/anonymize` — фронтенд
не получает 404, а понятный 503.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from .availability import INSTALL_HINT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/anonymize", tags=["anonymize"])

_MESSAGE = (
    "Модуль обезличивания недоступен: на сервере не установлены зависимости "
    "(pymorphy2, natasha). Остальные модули работают штатно. "
    "Администратору: " + INSTALL_HINT
)


@router.get("/status")
async def anonymize_status():
    """Доступен ли модуль. Позволяет интерфейсу не предлагать обезличивание."""
    from secretary.anomizer.src.anomizer import IMPORT_ERROR

    return {
        "available": False,
        "detail": _MESSAGE,
        "import_error": None if IMPORT_ERROR is None else str(IMPORT_ERROR),
    }


@router.post("")
async def anonymize_endpoint_unavailable(
    file: Optional[UploadFile] = File(default=None),
    report: Optional[str] = Query(default=None),
    orgs: Optional[str] = Query(default=None),
):
    raise HTTPException(status_code=503, detail=_MESSAGE)
