"""Проверка моделей, которые реально доступны в API провайдеров.

В настройках показываются только модели, которые можно выбрать в кабинете
проекта. Это пересечение двух источников:

* список моделей рабочего пространства (``GET /models``: у GigaChat — с учётом
  ``GIGACHAT_SCOPE``, у DeepSeek — ``GET /models`` аккаунта);
* разрешённые для стенда ID из ``GIGACHAT_AVAILABLE_MODELS`` /
  ``DEEPSEEK_AVAILABLE_MODELS``.

Второй список нужен потому, что рабочее пространство отдаёт и закрытые
freemium-модели (GigaChat 3 Pro, GigaChat 3 Lightning), которых нет в списке
моделей проекта. Если проверка API не удалась (нет сети, ключ не задан,
таймаут), список считается неизвестным, и интерфейс показывает разрешённый
каталог с пометкой в подсказке.

Результат кэшируется на API_MODELS_TTL секунд, чтобы открытие настроек не
обращалось к сети при каждом клике.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_AVAILABLE_MODELS,
    DEEPSEEK_BASE_URL,
    GIGACHAT_AVAILABLE_MODELS,
    GIGACHAT_CREDENTIALS,
    GIGACHAT_SCOPE,
)

logger = logging.getLogger(__name__)

API_MODELS_TTL = 600
API_MODELS_TIMEOUT = 10

# Модели, разрешённые для стенда; пустой список — ограничений нет.
ALLOWED_MODELS: dict[str, list[str]] = {
    "gigachat": GIGACHAT_AVAILABLE_MODELS,
    "deepseek": DEEPSEEK_AVAILABLE_MODELS,
}

_lock = threading.RLock()
_cache: dict[str, Any] = {"at": 0.0, "providers": {}}

# Модели эмбеддингов не используются как LLM в приложении.
_GIGACHAT_EMBEDDING_PREFIXES = ("embeddings", "gigaembeddings")


def _gigachat_model_ids() -> tuple[list[str] | None, str | None]:
    if not GIGACHAT_CREDENTIALS.strip():
        return None, "не задан GIGACHAT_CREDENTIALS"
    try:
        from gigachat import GigaChat

        client = GigaChat(
            credentials=GIGACHAT_CREDENTIALS,
            scope=GIGACHAT_SCOPE,
            verify_ssl_certs=False,
            timeout=API_MODELS_TIMEOUT,
        )
        response = client.get_models()
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"
    ids = []
    for model in getattr(response, "data", None) or []:
        model_id = getattr(model, "id_", None) or getattr(model, "id", None)
        if not model_id:
            continue
        if model_id.lower().startswith(_GIGACHAT_EMBEDDING_PREFIXES):
            continue
        ids.append(str(model_id))
    if not ids:
        return None, "API не вернул список моделей"
    return sorted(set(ids)), None


def _deepseek_model_ids() -> tuple[list[str] | None, str | None]:
    if not DEEPSEEK_API_KEY.strip():
        return None, "не задан DEEPSEEK_API_KEY"
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            timeout=API_MODELS_TIMEOUT,
        )
        response = client.models.list()
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"
    ids = sorted({str(item.id) for item in response.data if getattr(item, "id", None)})
    if not ids:
        return None, "API не вернул список моделей"
    return ids, None


def _apply_allowlist(provider: str, ids: list[str]) -> list[str]:
    """Оставляет только ID, разрешённые для стенда (пустой список — все)."""
    allowed = ALLOWED_MODELS.get(provider) or []
    if not allowed:
        return ids
    hidden = [model_id for model_id in ids if model_id not in allowed]
    if hidden:
        logger.info(
            "Скрыты модели %s: их нет в списке моделей проекта (%s)",
            provider,
            ", ".join(hidden),
        )
    return [model_id for model_id in ids if model_id in allowed]


def api_models(force: bool = False) -> dict[str, Any]:
    """Список доступных моделей API по провайдерам (None — проверка не удалась)."""
    with _lock:
        fresh = (time.monotonic() - _cache["at"]) < API_MODELS_TTL
        if fresh and not force:
            return _cache["providers"]
        providers: dict[str, Any] = {}
        for provider, probe in (
            ("gigachat", _gigachat_model_ids),
            ("deepseek", _deepseek_model_ids),
        ):
            ids, error = probe()
            if ids is not None:
                ids = _apply_allowlist(provider, ids)
                if not ids:
                    error = error or "нет разрешённых моделей"
                    ids = None
            providers[provider] = {
                "ids": ids,
                "error": error,
                "checked": ids is not None,
            }
            if error:
                logger.warning("Список моделей %s недоступен: %s", provider, error)
            else:
                logger.info("API %s: доступно моделей — %d", provider, len(ids or []))
        _cache["at"] = time.monotonic()
        _cache["providers"] = providers
        return providers


def offered_models(force: bool = False) -> list[dict[str, Any]]:
    """Каталог моделей, отфильтрованный по реальному списку API."""
    from core.settings import list_models

    status = api_models(force=force)
    result = []
    for model in list_models():
        provider = str(model.get("provider") or "")
        info = status.get(provider) or {}
        ids = info.get("ids")
        if ids is None:
            # Проверка не удалась — показываем только разрешённые для стенда.
            allowed = ALLOWED_MODELS.get(provider) or []
            if allowed and model["id"] not in allowed:
                continue
        elif model["id"] not in ids:
            logger.info("Модель %s скрыта: её нет в списке API %s", model["id"], provider)
            continue
        item = dict(model)
        item["api_checked"] = bool(info.get("checked"))
        result.append(item)
    return result


def is_offered(model_id: str) -> bool | None:
    """Доступна ли модель в API (None — проверить не удалось)."""
    from core.settings import model_provider

    provider = model_provider(model_id)
    allowed = ALLOWED_MODELS.get(provider) or []
    if allowed and model_id not in allowed:
        return False
    info = api_models().get(provider) or {}
    ids = info.get("ids")
    if ids is None:
        return None
    return model_id in ids
