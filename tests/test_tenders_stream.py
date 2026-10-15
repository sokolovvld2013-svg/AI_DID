"""Потоковый ответ (SSE) в модуле «Торги».

Проверка документации генерится LLM минутами. Раньше ответ отдавался одним
JSON-объектом, и длинная генерация упиралась в таймаут обратного прокси —
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


def sse_events(text: str) -> list[dict[str, Any]]:
    """Собрать JSON-события из SSE-выдачи (строки «data: {...}»)."""
    events = []
    for frame in text.split("\n\n"):
        for line in frame.split("\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


@pytest.fixture(autouse=True)
def open_tenders_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Токен доступа к «Торгам» не должен мешать тестам роутера."""
    import tenders.router as tr

    monkeypatch.setattr(tr, "TENDERS_ACCESS_TOKEN", "")


@pytest.fixture()
def stub_check_env(monkeypatch: pytest.MonkeyPatch) -> StubLLM:
    """Заглушки подготовки проверки: комплект документов уже «загружен»."""
    import tenders.router as tr

    llm = StubLLM(chunks=("📄 Документация", ": проверено [1]."))
    monkeypatch.setattr(tr, "get_llm", lambda: llm)
    monkeypatch.setattr(tr, "all_loaded", lambda sid: True)
    monkeypatch.setattr(
        tr,
        "get_documents",
        lambda sid: {"auction": {"doc_id": "doc-1", "filename": "Торг.docx"}},
    )
    monkeypatch.setattr(
        tr,
        "get_by_doc_id",
        lambda doc_id: {"doc_id": doc_id, "filename": "Торг.docx", "text": "текст"},
    )
    monkeypatch.setattr(
        tr,
        "build_check_context",
        lambda *args: (
            "контекст",
            [{"id": 1, "filename": "Торг.docx", "page": "Торговая документация"}],
            {"status": "warnings", "score": 80, "errors": [], "warnings": [{"message": "тест"}]},
        ),
    )
    monkeypatch.setattr(tr, "build_history_label", lambda *args: "Торг.docx")
    return llm


async def test_check_streams_deltas_then_done(client, stub_check_env):
    resp = await client.post("/tenders/query", json={"question": "Проверь", "mode": "check"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = sse_events(resp.text)
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "📄 Документация: проверено [1]."

    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["citations"] == [
        {"id": 1, "filename": "Торг.docx", "page": "Торговая документация", "file_id": None}
    ]
    assert done[0]["verification"] == {
        "status": "warnings",
        "score": 80,
        "errors_count": 0,
        "warnings_count": 1,
    }


async def test_check_done_writes_history(client, stub_check_env):
    await client.post("/tenders/query", json={"question": "Проверь", "mode": "check"})

    history = (await client.get("/tenders/history")).json()["history"]
    assert history
    last = history[0]
    assert last["mode"] == "check"
    assert last["label"] == "Торг.docx"
    assert "проверено" in last["response"]
    assert last["verification"]["score"] == 80


async def test_expert_streams_deltas_then_done(client, monkeypatch):
    import tenders.router as tr

    monkeypatch.setattr(
        tr, "get_llm", lambda: StubLLM(chunks=("Здравствуйте", "! Ответ по 135-ФЗ."))
    )

    resp = await client.post("/tenders/expert/query", json={"question": "Что такое задаток?"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = sse_events(resp.text)
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "Здравствуйте! Ответ по 135-ФЗ."

    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    # Итог проходит постобработку сервера (блок «Источники», чистка текста).
    assert "Здравствуйте" in done[0]["answer"]
    assert done[0]["verification"] is None

    history = (await client.get("/tenders/history")).json()["history"]
    assert history and history[0]["mode"] == "expert"


async def test_stream_llm_error_becomes_error_event(client, monkeypatch):
    import tenders.router as tr
    from core.llm_errors import LLMUserFacingError

    class FailingLLM:
        def stream(self, prompt, system_prompt=None, context=None):
            def _fail():
                yield "начало отчёта"
                raise LLMUserFacingError("Сервис LLM недоступен", RuntimeError("boom"))

            return _fail(), LLMUsage()

    monkeypatch.setattr(tr, "get_llm", lambda: FailingLLM())

    resp = await client.post("/tenders/expert/query", json={"question": "Вопрос"})

    # Ошибка в середине потока — это событие error при HTTP 200, а не JSON.
    assert resp.status_code == 200
    events = sse_events(resp.text)
    assert [e["text"] for e in events if e["type"] == "delta"] == ["начало отчёта"]
    errors = [e for e in events if e["type"] == "error"]
    assert errors and errors[0]["message"] == "Сервис LLM недоступен"

    # Сообщение об ошибке, как и раньше, попадает в историю сессии.
    history = (await client.get("/tenders/history")).json()["history"]
    assert history and history[0]["response"] == "Сервис LLM недоступен"


async def test_check_without_documents_returns_json_400(client):
    # Ошибки подготовки уходят до начала потока — обычным JSON, как раньше.
    resp = await client.post("/tenders/query", json={"question": "Проверь", "mode": "check"})

    assert resp.status_code == 400
    assert "торговую документацию" in resp.json()["detail"]
