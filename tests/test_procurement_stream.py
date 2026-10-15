"""Потоковый ответ (SSE) в модуле «Закупка 223-ФЗ».

Проверка документации и экспертная консультация генерятся LLM минутами.
Раньше ответ отдавался одним JSON-объектом и упирался в таймаут обратного
прокси — браузер получал 504 Gateway Time-out, хотя ответ добирался в
историю. Теперь сервер шлёт SSE-куски, и первый байт уходит сразу.
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
def open_procurement_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Токен доступа к «Закупкам» не должен мешать тестам роутера."""
    import procurement.router as pr

    monkeypatch.setattr(pr, "PROCUREMENT_ACCESS_TOKEN", "")


@pytest.fixture()
def stub_check_env(monkeypatch: pytest.MonkeyPatch) -> StubLLM:
    """Заглушки подготовки проверки: документация уже «загружена»."""
    import procurement.router as pr

    llm = StubLLM(chunks=("📄 Заявка", ": проверено [1] и [2]."))
    monkeypatch.setattr(pr, "get_llm", lambda: llm)
    monkeypatch.setattr(pr, "get_documentation", lambda sid: {"audit_id": "audit-1"})
    monkeypatch.setattr(
        pr,
        "get_by_audit_id",
        lambda audit_id: {"audit_id": audit_id, "filename": "Заявка.pdf"},
    )
    monkeypatch.setattr(
        pr,
        "build_check_context",
        lambda *args, **kwargs: (
            "контекст документации",
            [
                {"id": 1, "filename": "Заявка.pdf", "page": "Раздел 1"},
                {"id": 2, "filename": "Положение.pdf", "page": "п. 1"},
            ],
        ),
    )
    monkeypatch.setattr(pr, "enrich_policy_query", lambda q: q)
    monkeypatch.setattr(pr, "_build_policy_context", lambda *args, **kwargs: ("", []))
    monkeypatch.setattr(pr, "build_history_label", lambda *args: "Заявка.pdf")
    return llm


async def test_check_streams_deltas_then_done(client, stub_check_env):
    resp = await client.post("/procurement/query", json={"question": "Проверь", "mode": "check"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = sse_events(resp.text)
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "📄 Заявка: проверено [1] и [2]."

    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["citations"] == [
        {"id": 1, "filename": "Заявка.pdf", "page": "Раздел 1", "file_id": None},
        {"id": 2, "filename": "Положение.pdf", "page": "п. 1", "file_id": None},
    ]


async def test_check_done_writes_history(client, stub_check_env):
    await client.post("/procurement/query", json={"question": "Проверь", "mode": "check"})

    history = (await client.get("/procurement/history")).json()["history"]
    assert history
    last = history[0]
    assert last["mode"] == "check"
    assert last["label"] == "Заявка.pdf"
    assert "проверено" in last["response"]


async def test_check_without_sections_emits_done_message(client, monkeypatch, stub_check_env):
    # Разделы не извлечены — без генерации LLM поток завершается done-событием.
    import procurement.router as pr

    monkeypatch.setattr(pr, "build_check_context", lambda *args, **kwargs: ("", []))

    resp = await client.post("/procurement/query", json={"question": "Проверь", "mode": "check"})

    assert resp.status_code == 200
    events = sse_events(resp.text)
    assert not [e for e in events if e["type"] == "delta"]
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert "Не удалось извлечь разделы" in done[0]["answer"]


async def test_expert_streams_deltas_then_done(client, monkeypatch):
    import procurement.router as pr

    monkeypatch.setattr(
        pr, "get_llm", lambda: StubLLM(chunks=("Здравствуйте", "! Ответ по 223-ФЗ."))
    )
    monkeypatch.setattr(pr, "enrich_policy_query", lambda q: q)
    monkeypatch.setattr(
        pr,
        "_build_policy_context",
        lambda *args, **kwargs: (
            "Положение: п. 5",
            [{"id": 1, "filename": "Положение.pdf", "page": "п. 5"}],
        ),
    )

    resp = await client.post(
        "/procurement/query", json={"question": "Сроки подачи заявок?", "mode": "expert"}
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = sse_events(resp.text)
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "Здравствуйте! Ответ по 223-ФЗ."

    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    # Итог проходит постобработку сервера (блок «Источники», чистка текста).
    assert "Здравствуйте" in done[0]["answer"]

    history = (await client.get("/procurement/history")).json()["history"]
    assert history and history[0]["mode"] == "expert"


async def test_stream_llm_error_becomes_error_event(client, monkeypatch):
    import procurement.router as pr
    from core.llm_errors import LLMUserFacingError

    class FailingLLM:
        def stream(self, prompt, system_prompt=None, context=None):
            def _fail():
                yield "начало ответа"
                raise LLMUserFacingError("Сервис LLM недоступен", RuntimeError("boom"))

            return _fail(), LLMUsage()

    monkeypatch.setattr(pr, "get_llm", lambda: FailingLLM())
    monkeypatch.setattr(pr, "enrich_policy_query", lambda q: q)
    monkeypatch.setattr(pr, "_build_policy_context", lambda *args, **kwargs: ("", []))

    resp = await client.post("/procurement/query", json={"question": "Вопрос", "mode": "expert"})

    # Ошибка в середине потока — это событие error при HTTP 200, а не JSON.
    assert resp.status_code == 200
    events = sse_events(resp.text)
    assert [e["text"] for e in events if e["type"] == "delta"] == ["начало ответа"]
    errors = [e for e in events if e["type"] == "error"]
    assert errors and errors[0]["message"] == "Сервис LLM недоступен"

    # Сообщение об ошибке, как и раньше, попадает в историю сессии.
    history = (await client.get("/procurement/history")).json()["history"]
    assert history and history[0]["response"] == "Сервис LLM недоступен"


async def test_check_without_documents_returns_json_400(client, monkeypatch):
    import procurement.router as pr

    monkeypatch.setattr(pr, "get_documentation", lambda sid: None)

    # Ошибки подготовки уходят до начала потока — обычным JSON, как раньше.
    resp = await client.post("/procurement/query", json={"question": "Проверь", "mode": "check"})

    assert resp.status_code == 400
    assert "закупочную документацию" in resp.json()["detail"]
