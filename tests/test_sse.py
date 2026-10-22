"""Устойчивость потокового ответа (SSE) в core.sse.

Соединение между браузером и сервером рвёт VPN и прокси. Поэтому: во время
долгих пауз LLM клиенту шлются keep-alive события ping (первый байт уходит
сразу), а обрыв соединения не прерывает генерацию — проверка доходит до
конца в фоне и ответ попадает в историю.
"""

from __future__ import annotations

import asyncio
import json
import time

from core.sse import sse, stream_response


def _events_of(frame: str) -> list[dict]:
    """События из одного SSE-фрейма «data: {json}\n\n»."""
    out = []
    for line in frame.split("\n"):
        if line.startswith("data:"):
            out.append(json.loads(line[5:].strip()))
    return out


async def _collect(body) -> list[dict]:
    """Собрать все события из тела StreamingResponse."""
    events = []
    async for frame in body:
        events.extend(_events_of(frame))
    return events


async def test_pings_keep_connection_alive_during_generation():
    def gen():
        yield sse({"type": "delta", "text": "a"})
        time.sleep(0.25)
        yield sse({"type": "delta", "text": "b"})

    resp = stream_response(gen(), ping_interval=0.05)

    events = await _collect(resp.body_iterator)

    # Первый байт — ping сразу при старте; во время паузы LLM — ещё pings.
    assert events[0]["type"] == "ping"
    assert sum(1 for e in events if e["type"] == "ping") >= 2
    # Текст и порядок дельт при этом не страдают.
    assert [e["text"] for e in events if e["type"] == "delta"] == ["a", "b"]


async def test_stream_ends_with_done_event():
    def gen():
        yield sse({"type": "delta", "text": "текст"})
        yield sse({"type": "done", "answer": "текст", "citations": []})

    resp = stream_response(gen(), ping_interval=0.05)
    events = await _collect(resp.body_iterator)

    assert events[-1]["type"] == "done"
    assert events[-1]["answer"] == "текст"


async def test_client_disconnect_does_not_stop_generation():
    log: list[str] = []

    def gen():
        try:
            log.append("start")
            yield sse({"type": "delta", "text": "a"})
            time.sleep(0.25)
            log.append("tail")
            yield sse({"type": "done", "answer": "готово", "citations": []})
        finally:
            log.append("closed")

    resp = stream_response(gen(), ping_interval=0.05)
    first = await anext(resp.body_iterator)
    assert '"ping"' in first

    # Обрыв соединения клиентом: читатель закрывает поток чтения.
    await resp.body_iterator.aclose()

    await asyncio.sleep(0.6)
    # Генератор всё равно дошёл до конца: история и журнал дописываются —
    # именно на это опирается сообщение «Ответ сохранится в истории».
    assert "tail" in log
