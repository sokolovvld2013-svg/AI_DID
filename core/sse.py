"""Потоковый ответ LLM браузеру (SSE).

Долгие ответы (проверка документации, консультации) отдаются кусками по мере
генерации: браузер получает первые байты сразу, и запрос не упирается в
таймаут обратного прокси (504 Gateway Time-out), даже если генерация идёт
минутами. Протокол событий (строка «data: {json}» на событие):

- ``{"type": "ping"}`` — keep-alive: шлётся сразу при старте и далее каждые
  PING_INTERVAL_SEC секунд молчания. Первый байт уходит немедленно, а паузы
  между чанками LLM или ожидание первого чанка не выглядят для прокси или
  VPN-туннеля как мёртвое соединение. Фронтенд тип «ping» игнорирует;
- ``{"type": "delta", "text": "..."}`` — кусок текста ответа;
- ``{"type": "done", "answer": "...", "citations": [...]}`` — итог: очищенный
  ответ, источники и модульные поля (verification у проверок);
- ``{"type": "error", "message": "..."}`` — ошибка после начала потока.

Генератор событий выполняется в отдельном фоновом потоке и не зависит от
жизни клиентского соединения: если браузер, прокси или VPN-туннель оборвёт
запрос, проверка всё равно доходит до конца — ответ попадает в историю, а
расход токенов (через LLMUsage) — в журнал. Это повторяет поведение до
внедрения стриминга, когда ответ после 504 добирался в историю
(см. core.llm_client).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Iterable
from typing import Any

from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

# Заголовки отключают буферизацию в прокси впереди (nginx, Caddy и им
# подобные): без них прокси может держать ответ целиком и браузер снова
# увидит 504.
STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# Через сколько секунд молчания шлётся keep-alive событие ping. Запас ниже
# типичных таймаутов прокси и VPN-шлюзов (30–60 с), но достаточно редко,
# чтобы не шуметь.
PING_INTERVAL_SEC = 10.0


def sse(data: dict[str, Any]) -> str:
    """Одно SSE-сообщение: строка «data: {json}» и пустая строка-разделитель."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


class _StreamRun:
    """Фоновое выполнение генератора событий, не зависящее от клиента.

    Генератор целиком отрабатывает в отдельном потоке: клиентское соединение
    только читает готовые события. Обрыв соединения (F5, прокси, VPN) не
    прерывает генерацию — история и журнал дописываются в любом случае.
    """

    def __init__(self, events: Iterable[str]) -> None:
        self._events_src = events
        self._events: list[str] = []
        self._finished = False
        self._lock = threading.Lock()
        self._wake = asyncio.Event()
        self._started = False

    def _run(self, loop: asyncio.AbstractEventLoop) -> None:
        """Читает генератор событий до конца и рассылает оповещения циклу."""
        try:
            for event in self._events_src:
                with self._lock:
                    self._events.append(event)
                loop.call_soon_threadsafe(self._wake.set)
        except Exception:
            logger.exception("Потоковый ответ: генератор событий упал")
        finally:
            with self._lock:
                self._finished = True
            loop.call_soon_threadsafe(self._wake.set)

    async def _iterate(self, ping_interval: float) -> AsyncIterator[str]:
        """Отдаёт события клиенту, вставляя ping в паузы генерации."""
        yield sse({"type": "ping"})
        index = 0
        while True:
            with self._lock:
                pending = self._events[index:]
                index = len(self._events)
                finished = self._finished
            for event in pending:
                yield event
            if finished:
                return
            self._wake.clear()
            with self._lock:
                # Пока мы очищали флаг, могли прийти новые события.
                if index < len(self._events) or self._finished:
                    continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=ping_interval)
            except TimeoutError:
                # Долгое молчание (LLM думает): шлём keep-alive, чтобы соединение
                # не закрыли прокси, сеть или VPN-туннель.
                yield sse({"type": "ping"})

    def stream(self, ping_interval: float) -> AsyncIterator[str]:
        """Запускает фоновый разбор генератора и возвращает поток событий."""
        if not self._started:
            self._started = True
            loop = asyncio.get_running_loop()
            threading.Thread(target=self._run, args=(loop,), daemon=True).start()
        return self._iterate(ping_interval)


def stream_response(
    events: Iterable[str], ping_interval: float = PING_INTERVAL_SEC
) -> StreamingResponse:
    """Поток событий для браузера: куски текста, затем done/error.

    Генерация выполняется в фоне (см. _StreamRun): обрыв соединения клиентом
    не прерывает проверку — ответ добирается в историю.
    """
    run = _StreamRun(events)
    return StreamingResponse(
        run.stream(ping_interval),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )
