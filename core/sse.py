"""Потоковый ответ LLM браузеру (SSE).

Долгие ответы (проверка документации, консультации) отдаются кусками по мере
генерации: браузер получает первые байты сразу, и запрос не упирается в
таймаут обратного прокси (504 Gateway Time-out), даже если генерация идёт
минутами. Протокол событий (строка «data: {json}» на событие):

- ``{"type": "delta", "text": "..."}`` — кусок текста ответа;
- ``{"type": "done", "answer": "...", "citations": [...]}`` — итог: очищенный
  ответ, источники и модульные поля (verification у проверок);
- ``{"type": "error", "message": "..."}`` — ошибка после начала потока.

Расход токенов при потоковой генерации считается через LLMUsage
(см. core.llm_client) и передаётся в log_query явно.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from fastapi.responses import StreamingResponse

# Заголовки отключают буферизацию в прокси впереди (nginx и ему подобные):
# без них прокси может держать ответ целиком и браузер снова увидит 504.
STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse(data: dict[str, Any]) -> str:
    """Одно SSE-сообщение: строка «data: {json}» и пустая строка-разделитель."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_response(events: Iterable[str]) -> StreamingResponse:
    """Поток событий для браузера: куски текста, затем done/error."""
    return StreamingResponse(events, media_type="text/event-stream", headers=STREAM_HEADERS)
