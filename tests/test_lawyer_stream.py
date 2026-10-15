"""Потоковый ответ (SSE) в модуле «Юрист».

Проверка договора и ответы по базе знаний генерятся LLM минутами. Раньше
ответ отдавался одним JSON-объектом и упирался в таймаут обратного прокси —
браузер получал 504 Gateway Time-out, хотя ответ добирался в историю. Теперь
сервер шлёт SSE-куски, и первый байт уходит сразу.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.llm_client import LLMUsage


class StubLLM:
    """Заглушка LLM: заготовленные куски и фиксированный расход токенов."""

    def __init__(self, chunks: tuple[str, ...] = ("Ответ.",), tokens: int = 11):
        self._chunks = chunks
        self.tokens = tokens

    def stream(self, prompt, system_prompt=None, context=None):
        usage = LLMUsage(prompt_tokens=4, completion_tokens=7, total_tokens=self.tokens)
        return iter(self._chunks), usage


class StubRAG:
    """Заглушка поиска по базе знаний: готовый список фрагментов."""

    def __init__(self, hits: list[dict]):
        self._hits = hits

    def search(self, question: str) -> list[dict]:
        return self._hits

    def merge_neighbor_context(self, hit: dict) -> str:
        return hit.get("text") or ""


def sse_events(text: str) -> list[dict[str, Any]]:
    """Собрать JSON-события из SSE-выдачи (строки «data: {...}»)."""
    events = []
    for frame in text.split("\n\n"):
        for line in frame.split("\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


@pytest.fixture()
def stub_contract_env(monkeypatch: pytest.MonkeyPatch) -> StubLLM:
    """Заглушки подготовки проверки: договор уже «загружен»."""
    import lawyer.router as lr

    llm = StubLLM(chunks=("📄 Договор", ": проверено [1]."))
    monkeypatch.setattr(lr, "get_llm", lambda: llm)
    monkeypatch.setattr(
        lr,
        "get_contract",
        lambda sid: {"filename": "Договор.docx", "fragments": [{"text": "п. 1"}]},
    )
    monkeypatch.setattr(
        lr,
        "build_contract_context",
        lambda *args, **kwargs: (
            "контекст договора",
            [{"id": 1, "filename": "Договор.docx", "page": "п. 1"}],
        ),
    )
    monkeypatch.setattr(lr, "build_history_label", lambda contract: "Договор.docx")
    return llm


async def test_contract_streams_deltas_then_done(client, stub_contract_env):
    resp = await client.post("/lawyer/query", json={"question": "Проверь", "mode": "contract"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = sse_events(resp.text)
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "📄 Договор: проверено [1]."

    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["citations"] == [
        {"id": 1, "filename": "Договор.docx", "page": "п. 1", "file_id": None}
    ]


async def test_contract_done_writes_history(client, stub_contract_env):
    await client.post("/lawyer/query", json={"question": "Проверь", "mode": "contract"})

    history = (await client.get("/lawyer/history")).json()["history"]
    assert history
    last = history[0]
    assert last["mode"] == "contract"
    assert last["label"] == "Договор.docx"
    assert "проверено" in last["response"]


async def test_contract_without_document_returns_json_400(client, monkeypatch):
    import lawyer.router as lr

    monkeypatch.setattr(lr, "get_contract", lambda sid: None)

    # Ошибки подготовки уходят до начала потока — обычным JSON, как раньше.
    resp = await client.post("/lawyer/query", json={"question": "Проверь", "mode": "contract"})

    assert resp.status_code == 400
    assert "загрузите договор" in resp.json()["detail"]


async def test_kb_streams_deltas_then_done(client, monkeypatch):
    import lawyer.router as lr

    monkeypatch.setattr(
        lr,
        "_rag",
        lambda: StubRAG(
            [
                {
                    "text": "Раздел 1: порядок закупки",
                    "filename": "Регламент.pdf",
                    "page": 3,
                    "file_id": "file-1",
                }
            ]
        ),
    )
    monkeypatch.setattr(lr, "_select_relevant_hits", lambda question, hits: hits)
    monkeypatch.setattr(
        lr, "get_llm", lambda: StubLLM(chunks=("Ответ по регламенту ", "[1]."))
    )

    resp = await client.post("/lawyer/query", json={"question": "Как закупать?", "mode": "kb"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = sse_events(resp.text)
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "Ответ по регламенту [1]."

    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["answer"] == "Ответ по регламенту [1]."
    assert done[0]["citations"] == [
        {"id": 1, "filename": "Регламент.pdf", "page": 3, "file_id": "file-1"}
    ]

    history = (await client.get("/lawyer/history")).json()["history"]
    assert history and history[0]["mode"] == "kb"


async def test_kb_empty_base_emits_done_message(client, monkeypatch):
    import lawyer.router as lr

    monkeypatch.setattr(lr, "_rag", lambda: StubRAG([]))

    # Пустая база — без генерации LLM поток завершается done-событием.
    resp = await client.post("/lawyer/query", json={"question": "Что делать?", "mode": "kb"})

    assert resp.status_code == 200
    events = sse_events(resp.text)
    assert not [e for e in events if e["type"] == "delta"]
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert "База знаний пуста" in done[0]["answer"]

    history = (await client.get("/lawyer/history")).json()["history"]
    assert history and history[0]["mode"] == "kb"


async def test_stream_llm_error_becomes_error_event(client, monkeypatch):
    import lawyer.router as lr
    from core.llm_errors import LLMUserFacingError

    class FailingLLM:
        def stream(self, prompt, system_prompt=None, context=None):
            def _fail():
                yield "начало ответа"
                raise LLMUserFacingError("Сервис LLM недоступен", RuntimeError("boom"))

            return _fail(), LLMUsage()

    monkeypatch.setattr(
        lr,
        "_rag",
        lambda: StubRAG(
            [
                {
                    "text": "Раздел 1",
                    "filename": "Регламент.pdf",
                    "page": 3,
                    "file_id": "file-1",
                }
            ]
        ),
    )
    monkeypatch.setattr(lr, "_select_relevant_hits", lambda question, hits: hits)
    monkeypatch.setattr(lr, "get_llm", lambda: FailingLLM())

    resp = await client.post("/lawyer/query", json={"question": "Вопрос", "mode": "kb"})

    # Ошибка в середине потока — это событие error при HTTP 200, а не JSON.
    assert resp.status_code == 200
    events = sse_events(resp.text)
    assert [e["text"] for e in events if e["type"] == "delta"] == ["начало ответа"]
    errors = [e for e in events if e["type"] == "error"]
    assert errors and errors[0]["message"] == "Сервис LLM недоступен"

    # Сообщение об ошибке, как и раньше, попадает в историю сессии.
    history = (await client.get("/lawyer/history")).json()["history"]
    assert history and history[0]["response"] == "Сервис LLM недоступен"
