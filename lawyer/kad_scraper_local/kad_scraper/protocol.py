"""Протокол между сервером и локальными агентами.

Схемы лежат здесь, а не в сервере или агенте, потому что используются обоими:
сервер кладёт задание в очередь и принимает результат, агент читает задание и
отправляет отчёт. Объявление одно — разъехаться им негде.

Сервер и агент можно обновлять независимо: неизвестные поля игнорируются,
поэтому старый агент не падает на новом сервере и наоборот.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

JobState = Literal["queued", "running", "done", "failed", "cancelled"]

#: Как агент закончил работу. ``captcha`` и ``blocked`` — не ошибки агента,
#: а ограничение КАД: собранное сохранено, задачу можно повторить позже.
FinishState = Literal["ok", "partial", "captcha", "blocked", "error"]


def now_ts() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    """Короткий читаемый идентификатор: ``job-3f9a2c1b4d``."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def make_job(
    *,
    inn: str,
    limit: int | None = None,
    enrich: bool = False,
    headless: bool = False,
    note: str = "",
    requested_by: str = "",
) -> dict[str, Any]:
    """Создаёт задание в состоянии ``queued``."""
    return {
        "id": new_id("job"),
        "inn": str(inn).strip(),
        "limit": limit,
        "enrich": bool(enrich),
        "headless": bool(headless),
        "note": note,
        "requested_by": requested_by,
        "state": "queued",
        "created_at": now_ts(),
        "started_at": None,
        "finished_at": None,
        "agent": None,
        "result": None,
        "error": None,
    }


def make_result(
    *,
    job_id: str,
    agent: str,
    status: FinishState,
    records: list[dict[str, Any]],
    stop_reason: str,
    pages_visited: int = 0,
    total_cases: int = 0,
    captcha_hits: int = 0,
    warnings: list[str] | None = None,
    error: str | None = None,
    elapsed: float = 0.0,
) -> dict[str, Any]:
    """Создаёт отчёт агента по выполненному заданию.

    ``records`` — полные структурированные записи в формате
    :meth:`~kad_scraper.models.CaseRecord.as_dict`, а не склеенные строки
    Excel. По ним сервер собирает выгрузку сам, поэтому агент не зависит от
    версии Excel-оформления.
    """
    return {
        "job_id": job_id,
        "agent": agent,
        "status": status,
        "stop_reason": stop_reason,
        "records": records,
        "records_count": len(records),
        "pages_visited": pages_visited,
        "total_cases": total_cases,
        "captcha_hits": captcha_hits,
        "warnings": warnings or [],
        "error": error,
        "elapsed": elapsed,
        "finished_at": now_ts(),
    }


#: Во что превращается ``stop_reason`` парсера в статусе отчёта.
_STOP_TO_STATUS: dict[str, FinishState] = {
    "last_page": "ok",
    "limit": "ok",
    "max_pages": "partial",
    "no_results": "ok",
    "captcha": "captcha",
    "blocked": "blocked",
    "throttled": "blocked",
    "timeout": "partial",
    "page_failed": "partial",
    "error": "error",
}


def status_for_stop_reason(stop_reason: str, *, ok: bool) -> FinishState:
    """Подбирает статус отчёта по причине остановки сборщика.

    Сборщик различает «дел нет» и «сбор не удался», поэтому готовый парсер с
    нулём записей может быть как успешным (``no_results``), так и неудачным
    (``.limit``/``page_failed`` при пустом результате).
    """
    mapped = _STOP_TO_STATUS.get(stop_reason)
    if mapped:
        return mapped
    return "ok" if ok else "error"


#: Статусы, при которых собранное считается пригодным к выгрузке.
USABLE_STATUSES: frozenset[str] = frozenset({"ok", "partial", "captcha", "blocked"})

STATUS_LABELS: dict[str, str] = {
    "queued": "в очереди",
    "running": "выполняется",
    "done": "готово",
    "failed": "ошибка",
    "cancelled": "отменено",
}

FINISH_LABELS: dict[str, str] = {
    "ok": "сбор полный",
    "partial": "сбор частичный",
    "captcha": "остановлено капчей",
    "blocked": "остановлено блокировкой КАД",
    "error": "ошибка агента",
}