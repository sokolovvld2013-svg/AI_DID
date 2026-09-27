"""API общих настроек: выбор модели LLM, наименование компании и логотип."""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from core.api_models import api_models, is_offered, offered_models
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
        # иначе проверка доступности искала бы их в списке API как новые модели.
        canonical = canonical_model_id(payload.model) or payload.model
        if is_offered(canonical) is False:
            model = find_model(canonical)
            title = model["title"] if model else canonical
            raise HTTPException(
                status_code=400,
                detail=f"Модель {title} недоступна в API провайдера",
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
