"""Общая изоляция тестов от рабочего окружения.

Все переменные окружения выставляются до импорта main: модули читают
конфигурацию на этапе import, поэтому переопределение после импорта не поможет.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

# Тесты приложения не должны зависеть от локального .env разработчика. В
# production-файле KAD включается отдельно, а тест маршрутов без фичи должен
# оставаться детерминированным.
os.environ.setdefault("KAD_AGENT_ENABLED", "false")

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Временное хранилище на весь запуск сессии: пользовательские users.json,
# app_settings.json и chroma_data не должны попадать под тесты.
_TMP = Path(tempfile.mkdtemp(prefix="did-tests-"))

os.environ.update(
    {
        "AUTH_ENABLED": "false",
        "AUTH_SECRET": "test-secret-not-for-production",
        "AUTH_COOKIE_SECURE": "false",
        "USERS_FILE": str(_TMP / "users.json"),
        "SETTINGS_FILE": str(_TMP / "app_settings.json"),
        "CHROMA_PERSIST_DIR": str(_TMP / "chroma"),
        "USER_LOGS_DIR": str(_TMP / "logs" / "user_logs"),
        "WHISPER_PRELOAD": "false",
        "AUTH_ADMIN_ONLY_SETTINGS": "false",
    }
)


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    """Каталог для файлов, которые пишет приложение."""
    return _TMP


@pytest.fixture(scope="session")
def app():
    """Приложение без lifespan: сеть, Chroma и Whisper не поднимаются."""
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture()
async def client(app) -> AsyncIterator:
    """HTTP-клиент поверх ASGI без сети и lifespan."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture()
def offline_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Заглушки для внешних запросов модуля настроек."""
    import core.settings_router as sr

    async def _no_models(*args, **kwargs):
        return {}

    async def _no_fx(*args, **kwargs):
        return {}

    monkeypatch.setattr(sr, "api_models", _no_models, raising=False)
    monkeypatch.setattr(sr, "fx_info", _no_fx, raising=False)
    yield
