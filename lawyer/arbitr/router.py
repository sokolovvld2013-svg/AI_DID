"""Маршруты вкладки «Арбитражные дела» в блоке «Юрист».

Кнопка здесь только ставит задание в очередь. Сбор выполняет локальный агент
на компьютере сотрудника, поэтому вкладка умеет показывать три разных
состояния: ждёт машину, машина выполняет, выгрузка готова.

Ответы разделены намеренно. Статус вкладка опрашивает каждые пару секунд, а
Excel - отдельным запросом.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from config import KAD_AGENT_ENABLED, KAD_ARBITR_MAX_RECORDS
from core.auth_router import require_user
from lawyer.arbitr.jobs import STATE_LABELS, JobError, get_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lawyer/arbitr", tags=["lawyer-arbitr"])


class CreateJob(BaseModel):
    inn: str = Field(min_length=10, max_length=12)
    # Полный сбор подробностей включён постоянно; поле оставлено для
    # совместимости со старыми клиентами API.
    enrich: bool = True
    limit: int | None = Field(default=None, ge=1)


def _enabled() -> None:
    """Пока фича выключена - маршрутов нет вовсе, чтобы не смущать интерфейс."""
    if not KAD_AGENT_ENABLED:
        raise HTTPException(404, "Парсер КАД не включён на сервере")


def _job_view(job: dict[str, Any]) -> dict[str, Any]:
    """Задание в виде, пригодном для интерфейса."""
    result = job.get("result") or {}
    return {
        "id": job["id"],
        "inn": job["inn"],
        "state": job["state"],
        "state_label": STATE_LABELS.get(job["state"], job["state"]),
        "requested_by": job.get("requested_by") or "",
        "agent": job.get("agent") or "",
        "enrich": bool(job.get("enrich")),
        "limit": job.get("limit"),
        "created_at": job["created_at"],
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "elapsed": round(job["finished_at"] - job["started_at"], 1)
        if job.get("finished_at") and job.get("started_at")
        else None,
        "records_count": int(result.get("records_count") or 0),
        "pages_visited": int(result.get("pages_visited") or 0),
        "captcha_hits": int(result.get("captcha_hits") or 0),
        "finish_label": result.get("label") or "",
        "warnings": result.get("warnings") or [],
        "error": job.get("error") or "",
        "has_file": bool(job.get("file_name")),
    }


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    """Сводка для вкладки: задания и машины одним запросом."""
    _enabled()
    store = get_store()
    jobs = [_job_view(j) for j in store.list_jobs(limit=20)]
    return {
        "enabled": True,
        "jobs": jobs,
        "agents": [
            {"name": a["name"], "owner": a.get("owner") or "", "busy": bool(a.get("job_id"))}
            for a in store.machines()
        ],
        "waiting": store.waiting_agents(),
        "max_records": KAD_ARBITR_MAX_RECORDS,
    }


@router.post("/jobs")
async def create_job(request: Request, payload: CreateJob) -> dict[str, Any]:
    """Поставить задание в очередь.

    Если ни одна машина не ждёт, задание всё равно создаётся: оно уйдёт
    первой подключившейся машине. Ошибкой это не считаем - человек нажал
    кнопку, а чужой компьютер сейчас свободен.
    """
    _enabled()
    principal = require_user(request)
    store = get_store()
    try:
        job = store.create_job(
            inn=payload.inn,
            requested_by=principal.login or "локально",
            limit=payload.limit,
            enrich=True,
        )
    except JobError as exc:
        raise HTTPException(400, str(exc)) from exc
    logger.info(
        "Задание КАД %s создано: ИНН %s, заказал %s",
        job["id"],
        job["inn"],
        job["requested_by"],
    )
    return {"job": _job_view(job), "agents_online": store.online_count()}


@router.get("/jobs/{job_id}/download")
async def download(job_id: str) -> FileResponse:
    """Отдать выгрузку Excel."""
    _enabled()
    store = get_store()
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Задание не найдено")
    path = store.file_path(job_id)
    if not path:
        raise HTTPException(404, "Файл ещё не пришёл от агента")
    filename = f"kad_{job['inn']}_{job_id.replace('job-', '')}.xlsx"
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=filename)


@router.delete("/jobs/{job_id}")
async def delete(request: Request, job_id: str) -> dict[str, Any]:
    """Удалить задание и его файлы.

    Разрешено заказавшему или администратору: чужая выгрузка нужна коллеге
    для работы, а чужие кнопки в чужих заданиях - лишнее.
    """
    _enabled()
    principal = require_user(request)
    store = get_store()
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Задание не найдено")
    is_owner = bool(principal.login) and job.get("requested_by") == principal.login
    # Пустой логин бывает при AUTH_ENABLED=false: входа нет вовсе, судить
    # некому, поэтому в такой среде удалять может кто угодно.
    may_delete = principal.is_admin or not principal.login or is_owner
    if not may_delete:
        raise HTTPException(403, "Удалить может автор задания или администратор")
    try:
        store.delete_job(job_id)
    except JobError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}
