"""API общих настроек: выбор модели LLM, наименование компании и логотип."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core.api_models import api_models, is_offered, offered_models
from core.app_time import now_app
from core.settings import (
    PRICES_UPDATED_AT,
    SettingsError,
    canonical_model_id,
    find_model,
    fx_info,
    get_settings,
    logo_info,
    provider_unavailable_reason,
    reset_logo,
    save_logo_bytes,
    update_settings,
)
from core.user_logs import ERROR_CODES
from core.user_logs import modules as user_log_modules
from core.user_logs import read_logs
from config import USER_LOGS_DIR, USER_LOGS_RETENTION_DAYS

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    company_name: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=64)


def _payload() -> dict:
    settings = get_settings()
    return {
        "company_name": settings["company_name"],
        "model": settings["model"],
        "logo": logo_info(),
        "models": offered_models(),
        "api": api_models(),
        "prices_updated_at": PRICES_UPDATED_AT,
        "fx": fx_info(),
    }


@router.get("")
async def read_settings():
    return _payload()


@router.get("/models")
async def read_models(refresh: bool = False):
    status = api_models(force=refresh)
    return {
        "models": offered_models(force=refresh),
        "api": status,
        "prices_updated_at": PRICES_UPDATED_AT,
        "fx": fx_info(),
        "providers": {
            name: {"available": provider_unavailable_reason(name) is None}
            for name in ("deepseek", "gigachat")
        },
    }


@router.post("")
async def write_settings(payload: SettingsUpdate):
    if payload.model:
        # Псевдонимы (GigaChat-Pro и т. п.) приводим к каноническому ID,
        # иначе проверка искала бы их в списке стенда как отдельные модели.
        canonical = canonical_model_id(payload.model) or payload.model
        if not is_offered(canonical):
            model = find_model(canonical)
            title = model["title"] if model else canonical
            raise HTTPException(
                status_code=400,
                detail=f"Модель {title} недоступна: её нет в списке моделей проекта",
            )
    try:
        update_settings(company_name=payload.company_name, model=payload.model)
    except SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _payload()


@router.post("/logo")
async def upload_logo(file: UploadFile = File(...)):
    """Замена логотипа в шапке: файл кладётся в static/img/logo_custom.<ext>."""
    try:
        data = await file.read()
        save_logo_bytes(data, source_name=file.filename or "")
    except SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _payload()


@router.delete("/logo")
async def delete_logo():
    """Возврат исходного логотипа static/img/logo.png."""
    try:
        reset_logo()
    except SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _payload()


@router.get("/logs")
async def read_user_logs(
    limit: int | None = None,
    module: str = "",
    status: str = "",
    days: int | None = None,
):
    """Логи пользователей за окно хранения (по умолчанию 30 дней), свежие сверху.

    ``limit`` не передан (или ``0``) — отдать все записи окна хранения; это
    вариант «все» в интерфейсе. Само значение сверху ограничивает окно хранения.
    """
    if not limit:
        limit = None
    return {
        "logs": read_logs(limit=limit, module=module, status=status, days=days),
        "retention_days": USER_LOGS_RETENTION_DAYS,
        "storage_dir": str(USER_LOGS_DIR),
        "modules": user_log_modules(),
        "error_codes": ERROR_CODES,
    }


@router.get("/logs.csv")
async def download_user_logs(days: int | None = None):
    """Выгрузка логов в CSV для Excel."""
    rows = read_logs(limit=100000, days=days)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        ["Дата и время", "Логин", "IP-адрес", "Модуль", "Вопрос", "Токены", "Статус", "Код ошибки", "Ошибка"]
    )
    for row in rows:
        code = row.get("error_code", "")
        writer.writerow(
            [
                row.get("ts", ""),
                row.get("login", ""),
                row.get("ip", ""),
                row.get("module", ""),
                row.get("question", ""),
                row.get("tokens", 0),
                row.get("status", ""),
                code,
                ERROR_CODES.get(code, ""),
            ]
        )
    filename = f"user-logs-{now_app().strftime('%Y-%m-%d')}.csv"
    return Response(
        content="\ufeff" + buffer.getvalue(),  # BOM — чтобы Excel понял кириллицу
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
