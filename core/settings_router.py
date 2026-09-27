"""API общих настроек: выбор модели LLM и наименование компании."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.settings import (
    PRICES_UPDATED_AT,
    SettingsError,
    fx_info,
    get_settings,
    list_models,
    provider_unavailable_reason,
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
        "models": list_models(),
        "prices_updated_at": PRICES_UPDATED_AT,
        "fx": fx_info(),
    }


@router.get("")
async def read_settings():
    return _payload()


@router.get("/models")
async def read_models():
    return {
        "models": list_models(),
        "prices_updated_at": PRICES_UPDATED_AT,
        "fx": fx_info(),
        "providers": {
            name: {"available": provider_unavailable_reason(name) is None}
            for name in ("deepseek", "gigachat")
        },
    }


@router.post("")
async def write_settings(payload: SettingsUpdate):
    try:
        update_settings(company_name=payload.company_name, model=payload.model)
    except SettingsError as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(e)) from e
    return _payload()
