"""Очередь заданий парсера КАД для блока «Юрист».

Задание создаёт пользователь в интерфейсе на сервере, а выполняет локальный
агент на компьютере сотрудника. С VPS в КАД не ходят: там нет видимого
браузера, а капчу нужно решать человеком, и IP дата-центровый КАД встречает
блокировкой. Сервер только ставит задание в очередь, отдаёт его агенту и
хранит то, что агент прислал.

Состояние лежит на диске, а не в памяти: приложение перезапускается systemd
при падении, а обход на 787 дел иначе пропадёт вместе с выгрузкой. Каждое
задание - отдельный каталог с ``job.json`` и ``result.xlsx``; Excel собирает
агент на своей машине и присылает готовым файлом.

Агенты ждут задание обычным long polling: держу запрос открытым и отдаю
задание сразу, как только оно появилось. Это дешевле опроса с интервалом и не
тратит запросы, пока очередь пуста.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from config import (
    KAD_AGENT_HEARTBEAT,
    KAD_AGENT_STALE,
    KAD_ARBITR_DATA_DIR,
    KAD_ARBITR_MAX_RECORDS,
    KAD_ARBITR_QUEUE_TTL_HOURS,
    KAD_ARBITR_RETENTION_DAYS,
)

logger = logging.getLogger(__name__)

#: Состояния задания. ``expired`` - задание простояло в очереди дольше TTL и
#: было снято, чтобы мёртвый хвост не занимал место в очереди.
STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_EXPIRED = "expired"

#: Статусы, при которых собранное считается пригодным: парсер сам различает
#: «дел нет» и «сбор не удался», поэтому нулевой результат бывает успешным.
USABLE_STATUSES: frozenset[str] = frozenset({"ok", "partial", "captcha", "blocked"})

FINISH_LABELS: dict[str, str] = {
    "ok": "сбор полный",
    "partial": "сбор частичный",
    "captcha": "остановлено капчей",
    "blocked": "остановлено блокировкой КАД",
    "error": "ошибка агента",
}

STATE_LABELS: dict[str, str] = {
    STATE_QUEUED: "в очереди",
    STATE_RUNNING: "выполняется",
    STATE_DONE: "готово",
    STATE_FAILED: "ошибка",
    STATE_EXPIRED: "истекло",
}

_INN_RE = re.compile(r"^\d{10}(\d{2})?$")
_JOB_ID_RE = re.compile(r"^job-[0-9a-f]{10}$")

#: Сколько раз задание можно вернуть в очередь, прежде чем признать ошибкой.
#: Без предела задание с неисправной машиной ходило бы по кругу вечно.
MAX_ATTEMPTS = 3

#: Как часто разрешено проходить по диску в поисках старых выгрузок. Уборка
#: случается по поводу (создание задания, открытие списка), поэтому без
#: ограничения частоты список заданий дёргал бы обход каталога на каждый клик.
CLEANUP_INTERVAL_SEC = 3600.0


class JobError(ValueError):
    """Ошибка задания, видна пользователю как 400."""


def _atomic_write(path: Path, payload: bytes) -> None:
    """Записать файл целиком, чтобы обрыв процесса не оставил половину.

    Пишем рядом временный файл и подменяем им настоящий: читатель либо видит
    прежнее содержимое, либо новое, но не обрезанный кусок.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class JobStore:
    """Очередь заданий и состояние машин-агентов.

    Один экземпляр на процесс приложения. Блокировка обычная, из
    ``threading``: маршруты агента и интерфейса крутятся в одном event loop,
    но состояние пишут и из фоновых потоков агента, поэтому ``asyncio.Lock``
    тут не подошёл бы. Замок никогда не удерживается через ``await``.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        stale_sec: float = KAD_AGENT_STALE,
        heartbeat_sec: float = KAD_AGENT_HEARTBEAT,
        retention_days: float = KAD_ARBITR_RETENTION_DAYS,
        queue_ttl_hours: float = KAD_ARBITR_QUEUE_TTL_HOURS,
        max_records: int = KAD_ARBITR_MAX_RECORDS,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.stale_sec = stale_sec
        self.heartbeat_sec = heartbeat_sec
        self.retention_days = retention_days
        self.queue_ttl_sec = queue_ttl_hours * 3600.0
        self.max_records = max_records
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        # Машины, которые подтвердили связь: имя, логин владельца, время
        # последнего heartbeat и текущее задание.
        self._machines: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._last_cleanup = 0.0
        # Машины, которые сейчас держат открытый запрос в ожидании задания.
        self._waiters: dict[str, asyncio.Future[str]] = {}

    # ---------- работа с диском ----------

    def _job_dir(self, job_id: str) -> Path:
        return self.data_dir / job_id

    def _read_json(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_json(self, path: Path, payload: Any) -> None:
        _atomic_write(path, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _load_locked(self) -> None:
        """Перечитать каталог заданий. Вызывается один раз при первом обращении.

        При перезапуске приложения это единственный способ узнать про задания,
        которые уже в работе: агент держит открытый запрос и ждёт ответа.
        """
        if self._loaded:
            return
        self._loaded = True
        self.data_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for entry in self.data_dir.iterdir():
            if not entry.is_dir() or not _JOB_ID_RE.match(entry.name):
                continue
            job = self._read_json(entry / "job.json")
            if not isinstance(job, dict) or not job.get("id"):
                logger.warning("Пропущен повреждённый каталог задания: %s", entry.name)
                continue
            # Задание, о котором все машины забыли (сервер выключили посреди
            # сбора), возвращаем в очередь: иначе оно навсегда осталось бы
            # «выполняется», хотя браузер уже закрыт.
            if job.get("state") == STATE_RUNNING and self._agent_gone_locked(job, now):
                self._requeue_locked(job, "агент не отвечал, задание вернулось в очередь")
            self._jobs[job["id"]] = job
        if self._jobs:
            logger.info("Загружено заданий парсера КАД: %d", len(self._jobs))

    # ---------- состояние машин ----------

    def _agent_gone_locked(self, job: dict[str, Any], now: float) -> bool:
        last = job.get("agent_last_seen") or job.get("started_at") or job.get("created_at")
        return not last or (now - float(last)) > self.stale_sec

    def _online_agents_locked(self, now: float) -> list[dict[str, Any]]:
        """Живые машины: подтверждали связь не дольше ``stale_sec`` назад."""
        return list(self._agents_locked(now).values())

    def _agents_locked(self, now: float) -> dict[str, dict[str, Any]]:
        return {
            name: agent
            for name, agent in self._machines.items()
            if self._agent_alive_locked(agent, now)
        }

    def _agent_alive_locked(self, agent: dict[str, Any], now: float) -> bool:
        return (now - agent["last_seen"]) <= self.stale_sec

    def touch_agent_locked(self, name: str, owner: str | None = None) -> dict[str, Any]:
        """Отметить машину живой. Вызывается из heartbeat и из ``take``."""
        agent = self._machines.get(name)
        if agent is None:
            agent = {"name": name, "owner": "", "last_seen": 0.0, "job_id": None}
            self._machines[name] = agent
        agent["last_seen"] = time.time()
        # Логин владельца приходит один раз и не меняется: он нужен, чтобы
        # отдать задание той машине, за которой стоит нажавший кнопку.
        if owner and not agent.get("owner"):
            agent["owner"] = owner
        return agent

    def machines(self) -> list[dict[str, Any]]:
        with self._lock:
            now = time.time()
            self._forget_dead_machines_locked(now)
            # Копии: наружу не должны уезжать ссылки на внутреннее состояние,
            # иначе маршрут мог бы дописать в машину мимо замка.
            return sorted(
                (dict(a) for a in self._online_agents_locked(now)),
                key=lambda a: a["name"],
            )

    def _forget_dead_machines_locked(self, now: float) -> None:
        for name in [n for n, a in self._machines.items() if not self._agent_alive_locked(a, now)]:
            del self._machines[name]

    def heartbeat(self, name: str, owner: str | None = None, **extra: Any) -> dict[str, Any]:
        """Подтверждение живости. Должен быть отдельным запросом во время сбора."""
        with self._lock:
            self._load_locked()
            agent = self.touch_agent_locked(name, owner)
            if extra.get("job_id"):
                job = self._jobs.get(str(extra["job_id"]))
                # Подтверждение приходит с именем машины, которой выдали задание:
                # чужое подтверждение не должно продлевать чужую работу.
                if job and job.get("agent") == name:
                    job["agent_last_seen"] = agent["last_seen"]
                    self._write_job_locked(job)
            self._reclaim_stale_locked(now=time.time())
            return {
                "ok": True,
                "agents": len(self._online_agents_locked(agent["last_seen"])),
            }

    # ---------- задания ----------

    def _write_job_locked(self, job: dict[str, Any]) -> None:
        self._write_json(self._job_dir(job["id"]) / "job.json", job)

    def create_job(
        self,
        *,
        inn: str,
        requested_by: str = "",
        limit: int | None = None,
        enrich: bool = True,
        note: str = "",
    ) -> dict[str, Any]:
        """Поставить задание в очередь.

        ``headless`` в протоколе есть, но здесь всегда ``False``: обход на
        сервере без видимого браузера гарантированно упирается в капчу, а
        сотрудник не может её увидеть.
        """
        digits = re.sub(r"\D", "", inn or "")
        if not _INN_RE.match(digits):
            raise JobError("ИНН должен состоять из 10 или 12 цифр")
        if limit is not None:
            if limit < 1:
                raise JobError("Ограничение должно быть положительным числом")
            limit = min(int(limit), self.max_records)
        job = {
            "id": f"job-{uuid.uuid4().hex[:10]}",
            "inn": digits,
            "limit": limit,
            "enrich": bool(enrich),
            "headless": False,
            "note": (note or "").strip()[:200],
            "requested_by": requested_by,
            "state": STATE_QUEUED,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "agent": None,
            "agent_last_seen": None,
            "attempts": 0,
            "result": None,
            "error": None,
            "file_name": None,
        }
        with self._lock:
            self._load_locked()
            self._maybe_cleanup_locked()
            self._jobs[job["id"]] = job
            self._write_job_locked(job)
            self._dispatch_locked()
        return dict(job)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._load_locked()
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            self._load_locked()
            self._maybe_cleanup_locked()
            ordered = sorted(
                self._jobs.values(),
                key=lambda j: j["created_at"],
                reverse=True,
            )
            return [dict(j) for j in ordered[:limit]]

    def delete_job(self, job_id: str) -> bool:
        """Убрать задание и его файлы. Выполняемое удалять нельзя."""
        with self._lock:
            self._load_locked()
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job["state"] == STATE_RUNNING:
                raise JobError("Задание ещё выполняется - дождитесь окончания")
            self._jobs.pop(job_id, None)
            shutil.rmtree(self._job_dir(job_id), ignore_errors=True)
            return True

    def file_path(self, job_id: str) -> Path | None:
        with self._lock:
            self._load_locked()
            job = self._jobs.get(job_id)
            if not job or not job.get("file_name"):
                return None
            path = self._job_dir(job_id) / "result.xlsx"
            return path if path.exists() else None

    # ---------- приём результатов ----------

    def save_result(self, job_id: str, report: dict[str, Any], agent: str) -> dict[str, Any]:
        with self._lock:
            self._load_locked()
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.get("agent") != agent:
                raise JobError("Задание выполняет другая машина")
            records = report.get("records")
            if records is None:
                records = []
            if not isinstance(records, list):
                raise JobError("Записи пришли не списком")
            if len(records) > self.max_records:
                raise JobError(
                    f"Слишком много дел в отчёте: {len(records)} при лимите {self.max_records}"
                )
            status = str(report.get("status") or "error")
            # Отчёт без единой записи при status=error - это ошибка агента, а не
            # пустой результат: «дел нет» приходит со status=ok.
            usable = status in USABLE_STATUSES
            job["result"] = {
                "status": status,
                "label": FINISH_LABELS.get(status, status),
                "records_count": int(report.get("records_count") or len(records)),
                "pages_visited": int(report.get("pages_visited") or 0),
                "total_cases": int(report.get("total_cases") or 0),
                "captcha_hits": int(report.get("captcha_hits") or 0),
                "stop_reason": report.get("stop_reason") or "",
                "warnings": list(report.get("warnings") or [])[:20],
                "elapsed": float(report.get("elapsed") or 0.0),
            }
            job["state"] = STATE_DONE if usable else STATE_FAILED
            job["finished_at"] = float(report.get("finished_at") or time.time())
            job["error"] = None if usable else (report.get("error") or report.get("stop_reason"))
            self._write_job_locked(job)
            self._release_machine_locked(agent, job)
            self._dispatch_locked()
            return dict(job)

    def save_file(self, job_id: str, agent: str, content: bytes) -> Path:
        """Принять выгрузку Excel. Присылает её агент, а не сервер."""
        with self._lock:
            self._load_locked()
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.get("agent") != agent:
                raise JobError("Задание выполняет другая машина")
            path = self._job_dir(job_id) / "result.xlsx"
            _atomic_write(path, content)
            job["file_name"] = path.name
            self._write_job_locked(job)
            return path

    # ---------- раздача заданий ----------

    def _pick_waiter_locked(self, owner: str) -> str | None:
        """Кому отдать задание: сперва машине нажавшего, потом любой свободной.

        Иначе сотрудник без собственного агента ждал бы результата, который
        собирает коллега, и удивлялся бы, почему у него не открылось окно.
        """
        waiters = list(self._waiters)
        if owner:
            for name in waiters:
                machine = self._machines.get(name)
                if machine and machine.get("owner") == owner:
                    return name
        return waiters[0] if waiters else None

    def _claim_locked(self, job: dict[str, Any], agent: str) -> None:
        job["state"] = STATE_RUNNING
        job["agent"] = agent
        job["started_at"] = time.time()
        job["agent_last_seen"] = time.time()
        job["attempts"] = int(job.get("attempts") or 0) + 1
        job["error"] = None
        machine = self._machines.get(agent)
        if machine:
            machine["job_id"] = job["id"]
        self._write_job_locked(job)

    def _dispatch_locked(self) -> None:
        """Раздать очередь машинам, которые уже ждут. Замок удерживается."""
        while self._waiters:
            queued = sorted(
                (j for j in self._jobs.values() if j["state"] == STATE_QUEUED),
                key=lambda j: j["created_at"],
            )
            if not queued:
                return
            job = queued[0]
            waiter = self._pick_waiter_locked(job.get("requested_by") or "")
            if not waiter:
                return
            future = self._waiters.pop(waiter, None)
            self._claim_locked(job, waiter)
            if future is not None and not future.done():
                future.set_result(job["id"])

    def _release_machine_locked(self, agent: str, job: dict[str, Any]) -> None:
        """Машина освободилась - снять задание и дать ей следующее."""
        machine = self._machines.get(agent)
        if machine and machine.get("job_id") == job["id"]:
            machine["job_id"] = None

    def _requeue_locked(self, job: dict[str, Any], reason: str) -> None:
        job["state"] = STATE_QUEUED
        job["agent"] = None
        job["agent_last_seen"] = None
        job["error"] = reason
        if int(job.get("attempts") or 0) >= MAX_ATTEMPTS:
            job["state"] = STATE_FAILED
            job["error"] = f"{reason} (попыток: {job['attempts']})"
            return
        self._write_job_locked(job)

    def _reclaim_stale_locked(self, *, now: float) -> None:
        """Вернуть в очередь задания, чья машина замолчала."""
        for job in self._jobs.values():
            if job["state"] == STATE_RUNNING and self._agent_gone_locked(job, now):
                agent = job.get("agent") or ""
                logger.warning("Задание %s: агент %s молчит, возвращаю в очередь", job["id"], agent)
                self._release_machine_locked(agent, job)
                self._requeue_locked(job, "агент перестал отвечать, задание вернулось в очередь")
        # Вернувшиеся задания нужно сразу раздать: путь через heartbeat тоже
        # возвращает их в очередь, и без этого шага ждущая машина висела бы
        # до своего следующего опроса.
        self._dispatch_locked()

    async def take(self, name: str, *, owner: str = "", hold: float = 30.0) -> dict[str, Any] | None:
        """Дождаться задания.

        Держит запрос открытым, чтобы задание уходило сразу по нажатию кнопки,
        а не через опрос с интервалом. ``hold`` - потолок ожидания: протухший
        запрос клиент всё равно повторит, а держать его вечно нельзя, иначе
        соединение через туннель переживёт перезагрузку и молча провиснет.
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            self._load_locked()
            now = time.time()
            self._reclaim_stale_locked(now=now)
            agent = self.touch_agent_locked(name, owner)
            if agent.get("job_id"):
                raise JobError("Машина уже выполняет задание")
            future: asyncio.Future[str] = loop.create_future()
            self._waiters[name] = future
            self._dispatch_locked()
            job_id = future.result() if future.done() else None

        if job_id is None:
            try:
                job_id = await asyncio.wait_for(asyncio.shield(future), timeout=hold)
            except TimeoutError:
                return None
            finally:
                with self._lock:
                    if self._waiters.get(name) is future:
                        del self._waiters[name]

        job = self.get_job(job_id)
        return job

    # ---------- уборка ----------

    def _maybe_cleanup_locked(self) -> None:
        now = time.time()
        if now - self._last_cleanup < CLEANUP_INTERVAL_SEC:
            return
        self._last_cleanup = now
        self._cleanup_locked(now)

    def _cleanup_locked(self, now: float) -> None:
        for job in self._jobs.values():
            # Простояло в очереди дольше суток без агента - снимаем, чтобы
            # старые задания не копились и не занимали очередь.
            if (
                job["state"] == STATE_QUEUED
                and (now - job["created_at"]) > self.queue_ttl_sec
            ):
                job["state"] = STATE_EXPIRED
                job["finished_at"] = now
                job["error"] = "Ни одна машина не взяла задание за отведённое время"
                self._write_job_locked(job)

        if self.retention_days <= 0:
            return
        deadline = now - self.retention_days * 86400.0
        for job_id, job in list(self._jobs.items()):
            if job["state"] == STATE_RUNNING or job["state"] == STATE_QUEUED:
                continue
            stamp = job.get("finished_at") or job.get("created_at")
            if stamp and stamp < deadline:
                self._jobs.pop(job_id, None)
                shutil.rmtree(self._job_dir(job_id), ignore_errors=True)
                logger.info("Выгрузка КАД удалена по сроку хранения: %s", job_id)

    def cleanup(self) -> int:
        """Убрать просроченное. Вызывается при старте и по таймеру."""
        with self._lock:
            self._load_locked()
            before = len(self._jobs)
            self._last_cleanup = time.time()
            self._cleanup_locked(time.time())
            return before - len(self._jobs)

    def waiting_agents(self) -> int:
        """Сколько машин сейчас держат открытый запрос."""
        with self._lock:
            return len(self._waiters)

    def online_count(self) -> int:
        with self._lock:
            self._load_locked()
            now = time.time()
            self._forget_dead_machines_locked(now)
            return len(self._online_agents_locked(now))


#: Экземпляр приложения. Тесты создают свой ``JobStore`` на временном каталоге.
_store: JobStore | None = None
_store_lock = threading.Lock()


def get_store() -> JobStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = JobStore(KAD_ARBITR_DATA_DIR)
        return _store
