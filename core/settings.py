"""Настройки приложения: наименование компании и выбор модели LLM.

Значения хранятся в JSON-файле рядом с приложением и применяются без
перезапуска сервера. Файл настроек не содержит секретов: доступность моделей
определяется по наличию ключей в переменных окружения, сами ключи наружу не
выдаются.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from config import (
    BASE_DIR,
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    GIGACHAT_CREDENTIALS,
    GIGACHAT_MODEL,
    LLM_PROVIDER,
)

logger = logging.getLogger(__name__)

SETTINGS_FILE = Path(
    os.getenv("SETTINGS_FILE", "").strip() or str(BASE_DIR / "app_settings.json")
)

DEFAULT_COMPANY_NAME = os.getenv("COMPANY_NAME", "").strip() or 'ФГУП "ДИД"'
MAX_COMPANY_NAME_LEN = 120

PRICES_UPDATED_AT = "2026-09-27"
DEEPSEEK_SOURCE_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
GIGACHAT_SOURCE_URL = "https://developers.sber.ru/docs/ru/gigachat/tariffs/legal-tariffs?mode=sync"
# Доклад команды GigaChat (ACL-2025, arXiv:2506.09440) — единственный
# источник с числами параметров: открытая линейка Lite (A3B).
GIGACHAT_PARAMS_SOURCE_URL = "https://arxiv.org/abs/2506.09440"
# Оценка состава GigaChat 3 Ultra по открытым весам GigaChat3-702B: обзор на
# Хабре. Сбер состав версии, доступной через API, не раскрывает.
GIGACHAT_ULTRA_PARAMS_SOURCE_URL = "https://habr.com/ru/articles/971864/"

PROVIDER_LABELS = {"deepseek": "DeepSeek", "gigachat": "GigaChat"}

# Пиковые часы DeepSeek в документации указаны по UTC (01:00–04:00 и
# 06:00–10:00) — пользователю показываем московское время (UTC+3).
DEEPSEEK_PEAK_NOTE = "вне пика / пик. Пик по Москве: 04:00–07:00 и 09:00–13:00, Пн–Пт"

# Каталог моделей. Цены — за 1 млн выходных токенов; для долларовых цен
# дополнительно считается рублевый эквивалент по курсу ЦБ (core/fx.py).
# Число параметров GigaChat Сбер не раскрывает: для Lite известны только
# открытые модели линейки (GigaChat-A3B — 20B, ~3,3B активных), для Ultra —
# открытая GigaChat3-702B (702B, ~36B активных).
MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "deepseek-flash",
        "provider": "deepseek",
        "title": "DeepSeek-V4.1-Flash",
        "aliases": ["deepseek-chat", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"],
        "params": "284B всего / 13B активных (MoE)",
        "context": "1 000 000 токенов",
        "max_output": "384 000 токенов",
        "price_currency": "$",
        "price_value": 0.6,
        "price_secondary": 1.2,
        "price_text": "$0,60 / $1,20",
        "price_note": DEEPSEEK_PEAK_NOTE,
        "source": DEEPSEEK_SOURCE_URL,
    },
    {
        "id": "deepseek-v4-pro",
        "provider": "deepseek",
        "title": "DeepSeek-V4-Pro-0813",
        "aliases": [],
        "params": "1,6T всего / 49B активных (MoE)",
        "context": "1 000 000 токенов",
        "max_output": "не указано",
        "price_currency": "$",
        "price_value": 1.98,
        "price_secondary": 3.96,
        "price_text": "$1,98 / $3,96",
        "price_note": DEEPSEEK_PEAK_NOTE,
        "source": DEEPSEEK_SOURCE_URL,
    },
    {
        "id": "GigaChat-3-Ultra",
        "provider": "gigachat",
        "title": "GigaChat 3 Ultra",
        "aliases": ["GigaChat Ultra"],
        "params": "≈702B всего / 36B активных (MoE)",
        "params_note": "оценка по открытым весам GigaChat3-702B; состав версии в API Сбер не раскрывает",
        "params_source": GIGACHAT_ULTRA_PARAMS_SOURCE_URL,
        "context": "не указано",
        "max_output": "—",
        "price_currency": "₽",
        "price_value": 0.0,
        "price_text": "не опубликована",
        "price_note": "тариф для юрлиц не опубликован: у Сбера модель заявлена как фримиум для физлиц",
        "source": GIGACHAT_SOURCE_URL,
    },
    {
        "id": "GigaChat-2",
        "provider": "gigachat",
        "title": "GigaChat 2 Lite",
        "aliases": ["GigaChat", "GigaChat Lite"],
        "params": "≈20B всего / 3,3B активных (MoE)",
        "params_note": "число параметров версии в API Сбер не раскрывает; значение — по открытой линейке Lite (GigaChat-A3B)",
        "params_source": GIGACHAT_PARAMS_SOURCE_URL,
        "context": "128 000 токенов",
        "max_output": "—",
        "price_currency": "₽",
        "price_value": 65.0,
        "price_text": "65 ₽",
        "price_note": "",
        "source": GIGACHAT_SOURCE_URL,
    },
    {
        "id": "GigaChat-2-Pro",
        "provider": "gigachat",
        "title": "GigaChat 2 Pro",
        "aliases": ["GigaChat-Pro"],
        "params": "не опубликовано",
        "params_note": "закрытая модель; Сбер указывает только параметры открытой линейки Lite",
        "params_source": GIGACHAT_PARAMS_SOURCE_URL,
        "context": "128 000 токенов",
        "max_output": "—",
        "price_currency": "₽",
        "price_value": 500.0,
        "price_text": "500 ₽",
        "price_note": "",
        "source": GIGACHAT_SOURCE_URL,
    },
    {
        "id": "GigaChat-2-Max",
        "provider": "gigachat",
        "title": "GigaChat 2 Max",
        "aliases": ["GigaChat-Max"],
        "params": "не опубликовано",
        "params_note": "закрытая модель; Сбер указывает только параметры открытой линейки Lite",
        "params_source": GIGACHAT_PARAMS_SOURCE_URL,
        "context": "128 000 токенов",
        "max_output": "—",
        "price_currency": "₽",
        "price_value": 650.0,
        "price_text": "650 ₽",
        "price_note": "",
        "source": GIGACHAT_SOURCE_URL,
    },
]

_lock = threading.RLock()


class SettingsError(ValueError):
    """Некорректные значения настроек."""


def _package_installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def provider_unavailable_reason(provider: str) -> str | None:
    """Причина, по которой провайдер нельзя выбрать (None — доступен)."""
    if provider == "deepseek":
        if not DEEPSEEK_API_KEY.strip():
            return "не задан DEEPSEEK_API_KEY"
        if not _package_installed("openai"):
            return "не установлен пакет openai"
        return None
    if provider == "gigachat":
        if not GIGACHAT_CREDENTIALS.strip():
            return "не заданы GIGACHAT_CREDENTIALS"
        if not _package_installed("gigachat"):
            return "не установлен пакет gigachat (pip install -r requirements.txt)"
        return None
    return f"неизвестный провайдер: {provider}"


def find_model(model_id: str | None) -> dict[str, Any] | None:
    """Модель по каноническому ID или одному из алиасов."""
    if not model_id:
        return None
    wanted = str(model_id).strip()
    lowered = wanted.lower()
    for model in MODEL_CATALOG:
        if model["id"].lower() == lowered:
            return model
        for alias in model["aliases"]:
            if alias.lower() == lowered:
                return model
    return None


def canonical_model_id(model_id: str | None) -> str | None:
    model = find_model(model_id)
    return model["id"] if model else None


def default_model_id() -> str:
    """Модель из конфигурации (.env), приведённая к каталогу."""
    raw = GIGACHAT_MODEL if LLM_PROVIDER == "gigachat" else DEEPSEEK_MODEL
    return canonical_model_id(raw) or raw


def model_provider(model_id: str | None) -> str:
    """Провайдер для идентификатора модели (с запасным вариантом)."""
    model = find_model(model_id)
    if model:
        return str(model["provider"])
    lowered = (model_id or "").strip().lower()
    if lowered.startswith("gigachat"):
        return "gigachat"
    if lowered.startswith("deepseek"):
        return "deepseek"
    return LLM_PROVIDER


def _format_rub(value: float) -> str:
    """Число с запятой в качестве десятичного разделителя."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",").replace(" ", "")


def _rub_price_text(model: dict[str, Any], rate: float) -> str:
    """Цена в рублях: пересчёт долларовых тарифов по курсу ЦБ."""
    value = float(model.get("price_value") or 0.0) * rate
    secondary = model.get("price_secondary")
    if secondary:
        return f"{_format_rub(value)} / {_format_rub(float(secondary) * rate)} ₽"
    return f"{_format_rub(value)} ₽"


def fx_info() -> dict[str, Any]:
    """Курс ЦБ для отображения цен в рублях."""
    from core.fx import usd_rub

    return usd_rub()


def list_models() -> list[dict[str, Any]]:
    """Каталог с признаком доступности и причиной блокировки."""
    rate = float(fx_info().get("rate") or 0.0)
    result = []
    for model in MODEL_CATALOG:
        reason = provider_unavailable_reason(str(model["provider"]))
        item = {k: v for k, v in model.items() if k != "aliases"}
        item["provider_label"] = PROVIDER_LABELS.get(str(model["provider"]), model["provider"])
        item["available"] = reason is None
        item["unavailable_reason"] = reason
        if str(model["price_currency"]) == "$" and rate > 0:
            item["price_text"] = _rub_price_text(model, rate)
            item["price_usd_text"] = model["price_text"]
        else:
            item["price_usd_text"] = ""
        result.append(item)
    return result


def _default_settings() -> dict[str, str]:
    return {"company_name": DEFAULT_COMPANY_NAME, "model": default_model_id()}


def _read_settings() -> dict[str, str]:
    settings = _default_settings()
    try:
        raw = SETTINGS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return settings
    except OSError as e:
        logger.warning("Не удалось прочитать %s: %s", SETTINGS_FILE, e)
        return settings
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Повреждён файл настроек %s: %s", SETTINGS_FILE, e)
        return settings
    if not isinstance(data, dict):
        return settings
    company = data.get("company_name")
    if isinstance(company, str) and company.strip():
        settings["company_name"] = company.strip()
    model = data.get("model")
    if isinstance(model, str) and model.strip():
        settings["model"] = canonical_model_id(model) or model.strip()
    return settings


def _write_settings(settings: dict[str, str]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
    tmp_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, SETTINGS_FILE)


def get_settings() -> dict[str, str]:
    with _lock:
        return _read_settings()


def get_company_name() -> str:
    return get_settings()["company_name"]


def get_selected_model() -> str:
    return get_settings()["model"]


def normalize_company_name(value: Any) -> str:
    if not isinstance(value, str):
        raise SettingsError("Наименование компании должно быть строкой")
    if any(ord(ch) < 32 for ch in value):
        raise SettingsError("Наименование компании не должно содержать служебные символы")
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        raise SettingsError("Наименование компании не может быть пустым")
    if len(cleaned) > MAX_COMPANY_NAME_LEN:
        raise SettingsError(
            f"Наименование компании длиннее {MAX_COMPANY_NAME_LEN} символов"
        )
    return cleaned


def update_settings(company_name: Any = None, model: Any = None) -> dict[str, str]:
    """Частичное обновление настроек с валидацией."""
    with _lock:
        settings = _read_settings()
        if company_name is not None:
            settings["company_name"] = normalize_company_name(company_name)
        if model is not None:
            if not isinstance(model, str) or not model.strip():
                raise SettingsError("Не выбрана модель")
            canonical = canonical_model_id(model)
            if not canonical:
                raise SettingsError(f"Неизвестная модель: {model}")
            reason = provider_unavailable_reason(model_provider(canonical))
            if reason:
                raise SettingsError(f"Модель недоступна: {reason}")
            settings["model"] = canonical
        _write_settings(settings)
        logger.info(
            "Настройки обновлены: компания=%r, модель=%s",
            settings["company_name"],
            settings["model"],
        )
        return settings
