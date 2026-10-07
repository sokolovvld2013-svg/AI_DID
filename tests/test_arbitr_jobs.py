"""Проверки очереди парсера КАД и маршрутов агента.

Тесты не ходят в сеть и не трогают рабочие данные: очередь живёт во временном
каталоге, а HTTP-клиент поднимается поверх ASGI без lifespan. Сети нет и у
проверки пароля агента, и у проверки раздачи задания - всё в памяти.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lawyer.arbitr.jobs import (
    MAX_ATTEMPTS,
    STATE_DONE,
    STATE_EXPIRED,
    STATE_FAILED,
    STATE_QUEUED,
    STATE_RUNNING,
    JobError,
    JobStore,
)

IVAN_TOKEN = "token-ivan"
PETR_TOKEN = "token-petr"
IVAN = "ivan-pc"
PETR = "petr-pc"
OWNER = "ivan"


@pytest.fixture()
def store(tmp_path: Path) -> JobStore:
    """Очередь в отдельном каталоге на один тест.

    Каталог именно ``tmp_path``, а не ``tmp_root``: тот живёт всю сессию, и
    задания из прошлых тестов попадали бы в очередь следующих.
    """
    return JobStore(tmp_path / "kad", stale_sec=1.0, retention_days=10, queue_ttl_hours=1)


def _report(job_id: str, *, records: int = 3, status: str = "ok", agent: str = IVAN) -> dict:
    """Тело отчёта ровно в том виде, в каком шлёт агент.

    Формат плоский, без конверта: ``agent.send_result`` делает ``json=report``.
    Если обернуть его в ``{"report": {...}}``, сервер примет пустой отчёт и
    пометит задание ошибочным - поэтому форма держится здесь явной.
    """
    return {
        "job_id": job_id,
        "agent": agent,
        "status": status,
        "stop_reason": "last_page",
        "records": [{"Номер дела": f"А40-{i}"} for i in range(records)],
        "records_count": records,
        "pages_visited": 2,
        "total_cases": records,
        "captcha_hits": 0,
        "error": None,
        "elapsed": 12.5,
    }


async def _wait_for_waiters(store: JobStore, count: int = 1) -> None:
    """Дождаться, пока машины встанут в ожидание.

    Опрос по ``waiting_agents`` вместо ``sleep``: проверка не зависит от того,
    как быстро выполнился первый шаг корутины.
    """
    for _ in range(300):
        if store.waiting_agents() >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("машины не встали в ожидание задания")


async def _claim(store: JobStore, *, inn: str = "7707083893", owner: str = OWNER, agent: str = IVAN) -> dict:
    """Создать задание и отдать его машине: обычный порядок работы.

    Машина встаёт в ожидание первая, и только потом появляется задание.
    Обратный порядок не годится: агент забрал бы работу мгновенно, и опрос
    в ``_wait_for_waiters`` не успел бы увидеть ожидающего.
    """
    waiter = asyncio.create_task(store.take(agent, owner=owner, hold=5))
    await _wait_for_waiters(store)
    store.create_job(inn=inn, requested_by=owner)
    return await asyncio.wait_for(waiter, timeout=2)


# ---------- очередь ----------

async def test_create_job_goes_to_queue(store: JobStore) -> None:
    job = store.create_job(inn="7707083893", requested_by=OWNER)

    assert job["state"] == STATE_QUEUED
    assert job["agent"] is None
    # headless на сервере всегда выключен: без видимого браузера обход
    # гарантированно упирается в капчу, которую никто не увидит.
    assert job["headless"] is False
    assert (store._job_dir(job["id"]) / "job.json").exists()


async def test_inn_is_cleaned_and_validated(store: JobStore) -> None:
    assert store.create_job(inn=" 7707-083893 ")["inn"] == "7707083893"

    with pytest.raises(JobError):
        store.create_job(inn="123")
    with pytest.raises(JobError):
        store.create_job(inn="77070838931234")


async def test_agent_takes_job_as_soon_as_it_appears(store: JobStore) -> None:
    """Главное обещание: нажатие кнопки не ждёт следующего опроса."""
    waiter = asyncio.create_task(store.take(IVAN, owner=OWNER, hold=5))
    await _wait_for_waiters(store)

    store.create_job(inn="7707083893", requested_by=OWNER)
    job = await asyncio.wait_for(waiter, timeout=2)

    assert job["state"] == STATE_RUNNING
    assert job["agent"] == IVAN


async def test_agent_gets_nothing_when_queue_is_empty(store: JobStore) -> None:
    assert await store.take(IVAN, owner=OWNER, hold=0.05) is None


async def test_job_prefers_owners_own_machine(store: JobStore) -> None:
    """Задание нажавшего уходит на его машину, а не будит коллегу."""
    colleague = asyncio.create_task(store.take(PETR, owner="petr", hold=0.6))
    mine = asyncio.create_task(store.take(IVAN, owner=OWNER, hold=5))
    await _wait_for_waiters(store, 2)

    store.create_job(inn="7707083893", requested_by=OWNER)
    mine_job = await asyncio.wait_for(mine, timeout=2)

    assert mine_job["agent"] == IVAN
    # Коллега остаётся свободным: его машина не должна уводиться в чужую
    # работу, пока у заказчика свободна своя.
    assert await asyncio.wait_for(colleague, timeout=2) is None


async def test_job_falls_back_to_another_machine(store: JobStore) -> None:
    """Своя машина занята - задание всё равно уходит, а не висит вечно."""
    busy = await _claim(store)
    assert busy["agent"] == IVAN

    waiter = asyncio.create_task(store.take(PETR, owner="petr", hold=5))
    await _wait_for_waiters(store)
    store.create_job(inn="7707083893", requested_by=OWNER)
    job = await asyncio.wait_for(waiter, timeout=2)

    assert job["agent"] == PETR


async def test_busy_machine_cannot_take_two_jobs(store: JobStore) -> None:
    await _claim(store)

    with pytest.raises(JobError):
        await store.take(IVAN, owner=OWNER, hold=0.05)


# ---------- результат ----------

async def test_result_is_stored(store: JobStore) -> None:
    job = await _claim(store)

    saved = store.save_result(job["id"], _report(job["id"]), IVAN)

    assert saved["state"] == STATE_DONE
    assert saved["result"]["records_count"] == 3


async def test_failed_status_is_not_marked_done(store: JobStore) -> None:
    job = await _claim(store)

    saved = store.save_result(job["id"], _report(job["id"], status="error"), IVAN)

    assert saved["state"] == STATE_FAILED
    assert saved["error"]


async def test_captcha_still_counts_as_usable(store: JobStore) -> None:
    """Капча - ограничение КАД, а не поломка: собранное должно сохраниться."""
    job = await _claim(store)

    saved = store.save_result(job["id"], _report(job["id"], records=40, status="captcha"), IVAN)

    assert saved["state"] == STATE_DONE


async def test_only_assigned_machine_may_report(store: JobStore) -> None:
    job = await _claim(store)

    with pytest.raises(JobError):
        store.save_result(job["id"], _report(job["id"], agent=PETR), PETR)


async def test_report_for_unknown_job(store: JobStore) -> None:
    with pytest.raises(KeyError):
        store.save_result("job-0000000000", _report("job-0000000000"), IVAN)


async def test_oversized_report_is_refused(store: JobStore) -> None:
    job = await _claim(store)
    report = _report(job["id"], records=1)
    report["records"] = [{"a": 1}] * (store.max_records + 1)

    with pytest.raises(JobError):
        store.save_result(job["id"], report, IVAN)


# ---------- зависшие машины и уборка ----------

async def test_silent_machine_returns_job_to_queue(store: JobStore) -> None:
    """Задание от машины, которая отвалилась, само возвращается в очередь."""
    first = await _claim(store)
    assert first["state"] == STATE_RUNNING

    # Коллега уже ждёт, но заданий в очереди нет - он терпеливо висит.
    waiter = asyncio.create_task(store.take(PETR, owner="petr", hold=5))
    await _wait_for_waiters(store)

    # Ждём, пока первая машина перестанет считаться живой (stale_sec=1).
    await asyncio.sleep(1.1)
    store.heartbeat(PETR)  # прогон уборки отдаёт потерянное ожидающему

    claimed = await asyncio.wait_for(waiter, timeout=2)

    assert claimed["id"] == first["id"]
    assert claimed["agent"] == PETR
    assert claimed["attempts"] == 2


async def test_job_gives_up_after_repeated_losses(store: JobStore) -> None:
    """Задание не должно ходить по кругу вечно, если машина каждый раз отваливается."""
    job = store.create_job(inn="7707083893", requested_by=OWNER)

    for _ in range(MAX_ATTEMPTS):
        # Задание уже в очереди, поэтому машина забирает его сразу.
        await asyncio.wait_for(store.take(IVAN, owner=OWNER, hold=5), timeout=2)
        await asyncio.sleep(1.1)
        store.heartbeat(IVAN)  # прогон уборки потерянных заданий

    assert store.get_job(job["id"])["state"] == STATE_FAILED


async def test_heartbeat_keeps_job_running(store: JobStore) -> None:
    """Пульс продлевает задание: длинный обход не отнимут у живой машины."""
    job = await _claim(store)

    await asyncio.sleep(0.8)
    store.heartbeat(IVAN, job_id=job["id"])
    await asyncio.sleep(0.4)
    store.heartbeat(IVAN, job_id=job["id"])

    assert store.get_job(job["id"])["state"] == STATE_RUNNING


async def test_queue_ttl_expires_forgotten_jobs(store: JobStore) -> None:
    job = store.create_job(inn="7707083893", requested_by=OWNER)
    _age(store, job["id"], "created_at", 7200)

    store.list_jobs()

    assert store.get_job(job["id"])["state"] == STATE_EXPIRED


async def test_retention_removes_old_exports(store: JobStore) -> None:
    job = await _claim(store)
    store.save_result(job["id"], _report(job["id"]), IVAN)
    _age(store, job["id"], "finished_at", 11 * 86400)

    store.list_jobs()

    assert store.get_job(job["id"]) is None
    assert not store._job_dir(job["id"]).exists()


async def test_running_job_survives_cleanup(store: JobStore) -> None:
    """Уборка не должна снести задание, которое прямо сейчас выполняется."""
    job = await _claim(store)
    store._last_cleanup = 0.0

    store.cleanup()

    assert store.get_job(job["id"]) is not None


async def test_state_survives_restart(tmp_path: Path) -> None:
    """При перезапуске приложения задание в работе не должно пропасть."""
    first = JobStore(tmp_path / "jobs", stale_sec=1.0)
    job = await _claim(first)

    # Новый процесс видит задание сразу, без всякой сетевой связи.
    second = JobStore(tmp_path / "jobs", stale_sec=60)
    restored = second.get_job(job["id"])

    assert restored is not None
    assert restored["state"] == STATE_RUNNING
    assert restored["agent"] == IVAN


async def test_delete_removes_files(store: JobStore) -> None:
    job = await _claim(store)
    store.save_result(job["id"], _report(job["id"]), IVAN)
    store.save_file(job["id"], IVAN, b"xlsx-bytes")

    assert store.delete_job(job["id"]) is True
    assert store.get_job(job["id"]) is None
    assert not store._job_dir(job["id"]).exists()


async def test_running_job_cannot_be_deleted(store: JobStore) -> None:
    job = await _claim(store)

    with pytest.raises(JobError):
        store.delete_job(job["id"])


def _age(store: JobStore, job_id: str, field: str, seconds: float) -> None:
    """Состарить поле задания в файле и в памяти.

    Правки только на диске мало: очередь держит задания в памяти и читает
    файл лишь один раз при старте, поэтому состарить надо оба места. Заодно
    сбрасываем защиту от частой уборки - иначе проверка ждала бы час перед
    тем, как уборка соизволит пройтись по диску.
    """
    value = time.time() - seconds
    path = store._job_dir(job_id) / "job.json"
    data = store._read_json(path)
    data[field] = value
    store._write_json(path, data)
    store._jobs[job_id][field] = value
    store._last_cleanup = 0.0


# ---------- HTTP: маршруты агента ----------

@pytest.fixture()
async def kad(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator:
    """Мини-приложение с включённым парсером и очередью во временном каталоге.

    Основное приложение при KAD_AGENT_ENABLED=false маршрутов не содержит, а
    переменные окружения после импорта config уже не действуют, поэтому
    собираем отдельный FastAPI и подменяем значения прямо в модулях.
    """
    from lawyer.arbitr import agent_api, jobs
    from lawyer.arbitr import router as ui_router

    monkeypatch.setattr(jobs, "_store", JobStore(tmp_path / "kad-http"), raising=False)
    monkeypatch.setattr(ui_router, "KAD_AGENT_ENABLED", True)
    monkeypatch.setattr(
        agent_api,
        "KAD_AGENT_TOKENS_BY_NAME",
        {IVAN: IVAN_TOKEN, PETR: PETR_TOKEN},
    )

    mini = FastAPI()
    mini.include_router(ui_router.router)
    mini.include_router(agent_api.router)
    transport = ASGITransport(app=mini)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac, jobs._store


async def test_ping_rejects_unknown_token(kad) -> None:
    ac, _ = kad

    assert (await ac.get("/api/agent/ping")).status_code == 403
    assert (
        await ac.get("/api/agent/ping", headers={"X-Agent-Token": "wrong"})
    ).status_code == 403

    ok = await ac.get("/api/agent/ping", headers={"X-Agent-Token": IVAN_TOKEN})

    assert ok.status_code == 200
    assert ok.json()["ok"] is True


async def test_unregistered_machine_is_refused(kad) -> None:
    ac, _ = kad

    response = await ac.get(
        "/api/agent/jobs/next",
        headers={"X-Agent-Token": IVAN_TOKEN},
        params={"agent": "hacker-pc", "hold": 1},
    )

    assert response.status_code == 403


async def test_token_of_other_machine_is_refused(kad) -> None:
    """Свой пароль не даёт выдать себя за другую машину."""
    ac, _ = kad

    response = await ac.get(
        "/api/agent/jobs/next",
        headers={"X-Agent-Token": PETR_TOKEN},
        params={"agent": IVAN, "hold": 1},
    )

    assert response.status_code == 403


async def test_empty_queue_returns_no_content(kad) -> None:
    ac, _ = kad

    response = await ac.get(
        "/api/agent/jobs/next",
        headers={"X-Agent-Token": IVAN_TOKEN},
        params={"agent": IVAN, "hold": 1},
    )

    assert response.status_code == 204


async def test_full_round_trip(kad) -> None:
    """Путь целиком: кнопка -> очередь -> агент -> отчёт -> файл -> скачивание."""
    ac, store = kad
    auth = {"X-Agent-Token": IVAN_TOKEN}

    created = await ac.post(
        "/lawyer/arbitr/jobs", json={"inn": "7707083893", "enrich": True}
    )
    assert created.status_code == 200
    job_id = created.json()["job"]["id"]

    claimed = await ac.get(
        "/api/agent/jobs/next",
        headers=auth,
        params={"agent": IVAN, "owner": OWNER, "hold": 1},
    )
    assert claimed.status_code == 200
    assert claimed.json()["id"] == job_id
    assert claimed.json()["enrich"] is True

    report = await ac.post(
        f"/api/agent/jobs/{job_id}/result",
        headers=auth,
        params={"agent": IVAN},
        json=_report(job_id, records=787),
    )
    assert report.status_code == 200
    assert report.json()["state"] == STATE_DONE

    upload = await ac.post(
        f"/api/agent/jobs/{job_id}/file",
        headers=auth,
        params={"agent": IVAN},
        files={"file": ("kad.xlsx", b"xlsx-content", "application/vnd.ms-excel")},
    )
    assert upload.status_code == 200

    status = (await ac.get("/lawyer/arbitr/status")).json()
    assert status["jobs"][0]["records_count"] == 787
    assert status["jobs"][0]["has_file"] is True

    download = await ac.get(f"/lawyer/arbitr/jobs/{job_id}/download")
    assert download.status_code == 200
    assert download.content == b"xlsx-content"
    assert ".xlsx" in download.headers["content-disposition"]

    assert (await ac.delete(f"/lawyer/arbitr/jobs/{job_id}")).status_code == 200
    assert store.get_job(job_id) is None


async def test_report_for_another_job_is_refused(kad) -> None:
    """Отчёт, в котором назван чужой job_id, не должен закрывать наше задание."""
    ac, _ = kad
    auth = {"X-Agent-Token": IVAN_TOKEN}
    created = await ac.post("/lawyer/arbitr/jobs", json={"inn": "7707083893"})
    job_id = created.json()["job"]["id"]
    claimed = await ac.get(
        "/api/agent/jobs/next",
        headers=auth,
        params={"agent": IVAN, "hold": 1},
    )
    assert claimed.json()["id"] == job_id

    mixed = _report("job-somebodyelse", records=5)
    response = await ac.post(
        f"/api/agent/jobs/{job_id}/result",
        headers=auth,
        params={"agent": IVAN},
        json=mixed,
    )

    assert response.status_code == 409


async def test_status_shows_job_while_queued_without_agents(kad) -> None:
    """Нет ни одной машины - задание всё равно создаётся и ждёт."""
    ac, _ = kad

    created = await ac.post("/lawyer/arbitr/jobs", json={"inn": "7707083893"})
    status = (await ac.get("/lawyer/arbitr/status")).json()

    assert created.status_code == 200
    assert status["jobs"][0]["state"] == STATE_QUEUED
    assert status["agents"] == []
    assert status["waiting"] == 0


async def test_result_from_other_machine_is_refused(kad) -> None:
    """Чужой агент не может подменить отчёт по заданию, даже со своим паролем."""
    ac, store = kad
    job = store.create_job(inn="7707083893", requested_by=OWNER)
    store._machines[IVAN] = {
        "name": IVAN,
        "owner": OWNER,
        "last_seen": time.time(),
        "job_id": job["id"],
    }

    response = await ac.post(
        f"/api/agent/jobs/{job['id']}/result",
        headers={"X-Agent-Token": PETR_TOKEN},
        params={"agent": PETR},
        json=_report(job["id"], agent=PETR),
    )

    assert response.status_code == 409


async def test_bad_inn_is_rejected_by_api(kad) -> None:
    ac, _ = kad

    response = await ac.post("/lawyer/arbitr/jobs", json={"inn": "123"})

    assert response.status_code == 422


async def test_download_without_file_is_not_found(kad) -> None:
    ac, store = kad
    job = store.create_job(inn="7707083893", requested_by=OWNER)

    response = await ac.get(f"/lawyer/arbitr/jobs/{job['id']}/download")

    assert response.status_code == 404


async def test_routes_absent_when_feature_disabled(client) -> None:
    """С выключенной настройкой маршрутов нет вовсе, а не 403."""
    assert (await client.get("/api/agent/ping")).status_code == 404
    assert (await client.get("/lawyer/arbitr/status")).status_code == 404


def test_token_parsing_reports_latin1_problem() -> None:
    """Опечатка в .env должна быть видна, а не выглядеть как «агент молчит»."""
    from config import _parse_agent_tokens

    tokens, issues = _parse_agent_tokens("ivan-pc:ok,petr-pc:токен")

    assert tokens["ivan-pc"] == "ok"
    assert tokens["petr-pc"] == ""
    assert issues and "petr-pc" in issues[0]