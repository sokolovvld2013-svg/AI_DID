"""Маршруты локального агента парсера КАД.

Агент — фоновая программа на компьютере сотрудника, которая выполняет задания
из очереди. Он ходит на сервер сам, поэтому входящие порты на машине сотрудника
открывать не нужно, а маршруты живут под ``/api/agent`` - тем же префиксом, что
уже зашит в агента, так что сам агент менять не пришлось.

Вход здесь не по сессии пользователя, а по токену машины: программа приходит
без браузера и cookie. Токен проверяется ``hmac.compare_digest`` - сравнение
секрета не должно зависеть от времени, иначе по задержке можно подбирать его
побайтово (как и в ``procurement/router.py``).
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Request, Response, UploadFile

from config import KAD_AGENT_HOLD, KAD_AGENT_TOKENS_BY_NAME, KAD_ARBITR_MAX_FILE_MB
from lawyer.arbitr.jobs import JobError, JobStore, get_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["kad-agent"])

TOKEN_HEADER = "X-Agent-Token"
MAX_UPLOAD_BYTES = KAD_ARBITR_MAX_FILE_MB * 1024 * 1024


def _token_of(request: Request) -> str:
    return request.headers.get(TOKEN_HEADER) or ""


def _check_machine(request: Request, name: str) -> None:
    """Проверить токен конкретной машины.

    Токен сверяется с тем, что заведено для *этого* имени машины. Иначе любой,
    у кого есть один валидный токен, назвался бы чужой машиной и получил бы
    задание не на свой компьютер - тогда имя машины в истории заданий, ради
    которого всё затевалось, ничего не значило бы.
    """
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "Не указано имя машины")
    expected = KAD_AGENT_TOKENS_BY_NAME.get(name, "")
    if not expected:
        logger.warning("Попытка входа с незаведённой машины: %s", name)
        raise HTTPException(403, "Машина не заведена на сервере")
    if not hmac.compare_digest(_token_of(request), expected):
        raise HTTPException(403, "Неверный пароль агента")


def _check_any_token(request: Request) -> None:
    """Проверка для ``ping``: подходит ли хоть один токен.

    Машина в ``ping`` своё имя ещё не передаёт, а ответ должен сказать лишь
    «сервер меня слышит». Перебор имён тут не нужен - неизвестный ответ
    пользователю ничего полезного не добавит.
    """
    token = _token_of(request)
    known = KAD_AGENT_TOKENS_BY_NAME
    if not known:
        raise HTTPException(403, "На сервере не заведены машины агентов")
    if not any(hmac.compare_digest(token, value) for value in known.values() if value):
        raise HTTPException(403, "Неверный пароль агента")


def _store() -> JobStore:
    return get_store()


@router.get("/ping")
async def ping(request: Request) -> dict[str, Any]:
    """Проверка связи при запуске. Не сообщает имён машин."""
    _check_any_token(request)
    return {"ok": True, "agents": _store().online_count()}


@router.post("/heartbeat")
async def heartbeat(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Подтверждение живости во время длинного сбора."""
    name = str(payload.get("agent") or "")
    _check_machine(request, name)
    result = _store().heartbeat(
        name,
        owner=str(payload.get("owner") or "") or None,
        job_id=str(payload.get("job_id") or "") or None,
    )
    return result


@router.get("/jobs/next")
async def next_job(request: Request) -> Any:
    """Дождаться задания. Пустая очередь - ``204``, без тела.

    Запрос висит на сервере, поэтому задание уходит сразу по нажатию кнопки, а
    не через опрос с интервалом. Время ожидания задаёт сервер, а не агент:
    только сервер знает лимиты своего туннеля.
    """
    name = request.query_params.get("agent", "")
    _check_machine(request, name)
    owner = request.query_params.get("owner", "")
    job = await _store().take(name, owner=owner, hold=KAD_AGENT_HOLD)
    if not job:
        return Response(status_code=204)
    return job


@router.post("/jobs/{job_id}/result")
async def save_result(
    request: Request,
    job_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Принять отчёт агента. Записи уезжают в отдельный файл."""
    name = request.query_params.get("agent") or str(payload.get("agent") or "")
    _check_machine(request, name)
    # Протокол плоский: агент шлёт сам отчёт (agent.send_result -> json=report),
    # без конверта. Проверяем лишь то, что отчёт не перепутан с другим заданием.
    reported_id = str(payload.get("job_id") or job_id)
    if reported_id != job_id:
        raise HTTPException(409, "Отчёт относится к другому заданию")
    try:
        job = _store().save_result(job_id, payload, name)
    except KeyError:
        raise HTTPException(404, "Задание не найдено") from None
    except JobError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "state": job["state"]}


@router.post("/jobs/{job_id}/file")
async def save_file(
    request: Request,
    job_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Принять выгрузку Excel.

    Excel собирает агент, а не сервер: иначе на VPS пришлось бы ставить pandas
    и openpyxl ради файла, который уже посчитан на машине сотрудника.
    """
    name = request.query_params.get("agent", "")
    _check_machine(request, name)
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Файл больше {KAD_ARBITR_MAX_FILE_MB} МБ")
    if not content:
        raise HTTPException(400, "Пустой файл")
    try:
        _store().save_file(job_id, name, content)
    except KeyError:
        raise HTTPException(404, "Задание не найдено") from None
    except JobError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "size": len(content)}