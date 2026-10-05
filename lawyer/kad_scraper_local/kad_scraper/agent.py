"""Локальный агент: выполняет задания, которые ставит сервер.

Агент намеренно живёт на компьютере сотрудника, а не на сервере. Обход КАД
завязан на три вещи, которых на сервере нет: видимый браузер, домашняя сеть и
живая сессия. Здесь же решается капча. Сервер только хранит очередь и
результаты.

Связь односторонняя: сервер не достучится до машины сотрудника (NAT, динамический
IP), поэтому опрос делает агент — он сам ходит на сервер за заданиями.

Запуск:

    python -m kad_scraper.agent --server https://сервер:8000 --token ПАРОЛЬ
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests

from . import protocol
from .runner import failed_report, run_collection

log = logging.getLogger("kad.agent")

#: Пауза перед повтором после обрыва связи с сервером. В норме её не бывает:
#: запрос сам висит до появления задания.
POLL_INTERVAL_SEC = 15.0

#: Сколько ждать ответа сервера, прежде чем считать его недоступным.
HTTP_TIMEOUT_SEC = 30.0

#: Держим задание в одном запросе дольше, чем типичный таймаут прокси между
#: клиентом и сервером. Если соединение всё же рвут, агент переспрашивает, но
#: задержки перед стартом не появляется: задание приходит по живому сокету.
HOLD_SEC = 90.0

#: Как часто подтверждать «я жив», пока идёт обход. Обход одного дела тянется
#: на минуты, за это время агент не держит очередь — без пульса сервер решит,
#: что машина умерла, и отдаст задание другому.
HEARTBEAT_SEC = 30.0

# Файл настроек рядом с launcher/setup.cmd. Переменные окружения, заданные
# снаружи, имеют приоритет над значениями из этого файла.
ENV_FILE = "agent.env"


def load_env_file(path: Path) -> list[str]:
    """Прочитать ``KEY=VALUE`` из файла настроек агента.

    Нужен для запуска из Планировщика заданий: там нет ни окна, ни возможности
    задать переменные окружения, а настройки и пароль машины должны где-то
    лежать. Реальные переменные окружения имеют приоритет - файл лишь
    дополняет их, чтобы можно было задать пароль снаружи и не светить его
    в репозитории.

    Разбираем сами, без python-dotenv: ради шести строк тянуть зависимость в
    машину каждого сотрудника незачем.
    """
    if not path.is_file():
        return []
    loaded: list[str] = []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


def default_agent_name() -> str:
    """Понятное имя машины: ``ivan-pc`` или ``WORKSTATION-04``.

    Имя видно в веб-интерфейсе сервера — по нему понятно, чей компьютер
    сейчас выполняет задание.
    """
    return platform.node() or "pc"


class AgentClient:
    """Тонкая обёртка над HTTP-обменом с сервером."""

    def __init__(
        self,
        server: str,
        token: str,
        *,
        timeout: float = HTTP_TIMEOUT_SEC,
        owner: str = "",
    ) -> None:
        self.server = server.rstrip("/")
        self.timeout = timeout
        #: Кто нажал кнопку. Пусто у машины «в общем пользовании».
        self.owner = owner
        self.session = requests.Session()
        # Пароль агента передаётся заголовком, а не в URL: иначе он попадёт
        # в логи веб-сервера вместе с путём запроса.
        self.session.headers["X-Agent-Token"] = token

    def _url(self, path: str) -> str:
        return f"{self.server}{path}"

    def ping(self) -> bool:
        """Проверяет, что сервер жив и пароль подходит.

        Любая сетевая ошибка — это тоже «не работает»: вызывающий код должен
        получить ``False`` и написать человеку понятное сообщение, а не
        трассировку из urllib3.
        """
        try:
            response = self.session.get(self._url("/api/agent/ping"), timeout=self.timeout)
        except requests.RequestException:
            return False
        return response.ok

    def heartbeat(self, agent: str, **extra: Any) -> None:
        payload = {"agent": agent, "platform": platform.platform(), **extra}
        if self.owner:
            payload["owner"] = self.owner
        self.session.post(
            self._url("/api/agent/heartbeat"), json=payload, timeout=self.timeout
        )

    def next_job(self, agent: str) -> dict[str, Any] | None:
        """Забирает задание. ``None`` — очередь пуста.

        Запрос висит на сервере до ``HOLD_SEC``: как только задание появится,
        ответ приходит немедленно. Поэтому нажатие кнопки в интерфейсе не
        ждёт следующего опроса — соединение уже открыто.

        Владелец (``self.owner``) едет в каждом запросе: сервер отдаёт задание
        в первую очередь машине нажавшего, а если она занята, любой свободной.
        """
        response = self.session.get(
            self._url("/api/agent/jobs/next"),
            params={
                "agent": agent,
                "platform": platform.platform(),
                "hold": HOLD_SEC,
                "owner": self.owner,
            },
            timeout=HOLD_SEC + 30.0,
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        job = response.json()
        return job if isinstance(job, dict) and job.get("id") else None

    def send_result(self, report: dict[str, Any], *, xlsx: Path | None) -> None:
        """Отправляет отчёт. XLSX уходит отдельным файлом, JSON — в теле."""
        response = self.session.post(
            self._url(f"/api/agent/jobs/{report['job_id']}/result"),
            params={"agent": report.get("agent", "")},
            json=report,
            timeout=max(self.timeout, 120.0),
        )
        response.raise_for_status()

        if xlsx and xlsx.exists():
            with xlsx.open("rb") as handle:
                upload = self.session.post(
                    self._url(f"/api/agent/jobs/{report['job_id']}/file"),
                    params={"agent": report.get("agent", "")},
                    files={"file": (xlsx.name, handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    timeout=max(self.timeout, 120.0),
                )
            upload.raise_for_status()
            log.info("Excel отправлен на сервер: %s", xlsx.name)


class KeepAlive:
    """Подтверждает «я жив», пока идёт обход.

    Обход одного дела занимает минуты, а в это время агент не висит на очереди.
    Без этого пульса сервер посчитал бы машину упавшей и отдал задание другому
    агенту — два браузера пошли бы в КАД одновременно, что почти гарантирует
    блокировку.
    """

    def __init__(
        self,
        client: AgentClient,
        agent: str,
        job_id: str,
        every: float = HEARTBEAT_SEC,
    ) -> None:
        self._client = client
        self._agent = agent
        self._job_id = job_id
        self._every = every
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> KeepAlive:
        def beat() -> None:
            # Пауза до первого пульса, а не после: сообщение «начал» сервер уже
            # получил при выдаче задания.
            while not self._stop.wait(self._every):
                try:
                    self._client.heartbeat(self._agent, job_id=self._job_id)
                except requests.RequestException as exc:
                    log.warning("Пульс не дошёл: %s", exc)

        self._thread = threading.Thread(target=beat, name="kad-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)


def _print_progress(progress: Any) -> None:
    """Компактный лог в консоль: одна строка на страницу и на карточку."""
    phase = progress.phase
    if phase == "Сбор данных":
        print(f"  [{progress.page}/{progress.total_pages or '?'}] {progress.message}", flush=True)
    elif phase == "Обогащение карточек":
        print(f"  карточка {progress.enriched}/{progress.enrich_total}: {progress.message}", flush=True)
    elif phase == "Завершено":
        print(f"  готово: {progress.message}", flush=True)


def handle_job(
    client: AgentClient,
    job: dict[str, Any],
    *,
    agent: str,
    output_dir: Path,
) -> None:
    """Выполняет одно задание и отправляет отчёт серверу."""
    job_id = job.get("id", "?")
    inn = str(job.get("inn", "")).strip()
    note = f" ({job['note']})" if job.get("note") else ""
    print(f"\n=== задание {job_id}: ИНН {inn}{note} ===", flush=True)

    if not inn.isdigit():
        client.send_result(
            protocol.make_result(
                job_id=job_id,
                agent=agent,
                status="error",
                records=[],
                stop_reason="error",
                error=f"некорректный ИНН: {inn!r}",
            ),
            xlsx=None,
        )
        return

    started = time.monotonic()
    try:
        with KeepAlive(client, agent, job_id):
            report = run_collection(
                inn=inn,
                limit=job.get("limit"),
                enrich=bool(job.get("enrich")),
                headless=bool(job.get("headless")),
                output_dir=output_dir,
                on_progress=_print_progress,
            )
    except Exception as exc:  # noqa: BLE001 — агент обязан доложить об аварии
        log.exception("Задание %s упало", job_id)
        report = failed_report(
            job_id=job_id, agent=agent, error=exc, elapsed=time.monotonic() - started
        )

    report["job_id"] = job_id
    report["agent"] = agent

    xlsx = report.pop("result_path", "") or ""
    xlsx_path = Path(xlsx) if xlsx else None

    print(
        f"  статус: {protocol.FINISH_LABELS.get(report['status'], report['status'])}, "
        f"записей: {report['records_count']}, причина: {report['stop_reason']}",
        flush=True,
    )
    for warning in report.get("warnings") or []:
        print(f"  ! {warning}", flush=True)

    client.send_result(report, xlsx=xlsx_path)


def run_loop(
    client: AgentClient,
    *,
    agent: str,
    output_dir: Path,
    once: bool = False,
    retry_delay: float = POLL_INTERVAL_SEC,
) -> int:
    """Основной цикл: ждать задание → выполнить → доложить → ждать дальше.

    ``retry_delay`` используется только после обрыва связи. В нормальном
    режиме паузы нет: запрос сам висит на сервере, пока задание не появится.
    """
    print(f"Агент «{agent}» подключён к {client.server}", flush=True)
    print("Браузер будет открываться видимым — капчу придётся решить вручную.", flush=True)

    while True:
        try:
            job = client.next_job(agent)
        except requests.RequestException as exc:
            log.warning("Связь с сервером прервана (%s), повтор через %s с", exc, retry_delay)
            if once:
                return 2
            time.sleep(retry_delay)
            continue

        if job is None:
            # Сервер не прислал задание за HOLD_SEC. Спрашиваем ещё раз.
            if once:
                print("Очередь пуста.", flush=True)
                return 0
            continue

        handle_job(client, job, agent=agent, output_dir=output_dir)

        if once:
            return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m kad_scraper.agent",
        description="Локальный агент парсера КАД: выполняет задания с сервера.",
    )
    parser.add_argument(
        "--env",
        default=ENV_FILE,
        help=f"файл настроек агента (по умолчанию {ENV_FILE})",
    )
    parser.add_argument(
        "--server",
        default=os.environ.get("KAD_AGENT_SERVER", ""),
        help="адрес сервера, например https://kad.example.com "
             "(или переменная окружения KAD_AGENT_SERVER)",
    )
    parser.add_argument(
        "--token", default=os.environ.get("KAD_AGENT_TOKEN", ""),
        help="пароль агента (или переменная окружения KAD_AGENT_TOKEN)",
    )
    parser.add_argument(
        "--name",
        default=os.environ.get("KAD_AGENT_NAME") or default_agent_name(),
        help="имя машины, по умолчанию — хост (или переменная KAD_AGENT_NAME)",
    )
    parser.add_argument(
        "--owner",
        default=os.environ.get("KAD_AGENT_OWNER", ""),
        help="логин сотрудника, которому принадлежит машина: его задания "
             "приходят первыми (или переменная KAD_AGENT_OWNER)",
    )
    parser.add_argument(
        "--log",
        default=os.environ.get("KAD_AGENT_LOG", ""),
        help="писать журнал в файл вместо окна консоли — обязательно для "
             "запуска из Планировщика заданий (или переменная KAD_AGENT_LOG)",
    )
    parser.add_argument(
        "--output", default="output", help="папка для чекпойнтов и Excel (по умолчанию output)"
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=POLL_INTERVAL_SEC,
        help="пауза после обрыва связи с сервером, с",
    )
    parser.add_argument(
        "--once", action="store_true", help="взять не больше одного задания и выйти"
    )
    parser.add_argument("--verbose", action="store_true", help="подробный лог")
    return parser


def env_path_from_argv(argv: list[str] | None) -> Path:
    """Путь к файлу настроек из ``--env``, без полного разбора аргументов.

    Отдельная мелочь нужна потому, что файл надо прочитать *до* разбора: иначе
    значения из него не попадут в те места, где аргумент не указан.
    """
    argv = argv or []
    for index, item in enumerate(argv):
        if item == "--env" and index + 1 < len(argv):
            return Path(argv[index + 1])
        if item.startswith("--env="):
            return Path(item.split("=", 1)[1])
    return Path(ENV_FILE)


def main(argv: list[str] | None = None) -> int:
    load_env_file(env_path_from_argv(argv))
    args = build_parser().parse_args(argv)
    _setup_logging(args)
    return _run(args)


def _setup_logging(args: argparse.Namespace) -> None:
    """Настроить вывод.

    Из Планировщика заданий агент запускается без окна, и писать в stdout некуда.
    Поэтому с ``--log`` всё идёт в rotating-файл: иначе агент молча падал бы
    при каждом запуске, не оставив следов.
    """
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    if args.log:
        from logging.handlers import RotatingFileHandler

        path = Path(args.log)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=2, encoding="utf-8"
        )
    else:
        handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root.addHandler(handler)


def _run(args: argparse.Namespace) -> int:
    if not args.server:
        print(
            "Не указан адрес сервера: --server или переменная KAD_AGENT_SERVER.",
            file=sys.stderr,
        )
        return 2

    if not args.token:
        print(
            "Не указан пароль агента: --token или переменная KAD_AGENT_TOKEN.",
            file=sys.stderr,
        )
        return 2

    # Токен едет в HTTP-заголовке, а стандарт разрешает там только latin-1.
    # Без проверки кириллический пароль уронил бы агента при первом запросе,
    # причём непонятно где — пароль ведь выглядит нормально.
    try:
        args.token.encode("latin-1")
    except UnicodeEncodeError:
        print(
            "Пароль агента должен состоять из латинских символов: "
            "кириллица не допускается в HTTP-заголовке.",
            file=sys.stderr,
        )
        return 2

    client = AgentClient(args.server, args.token, owner=args.owner)
    if not client.ping():
        print(f"Сервер {args.server} не отвечает или пароль неверный.", file=sys.stderr)
        return 2

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    return run_loop(
        client,
        agent=args.name,
        output_dir=output_dir,
        once=args.once,
        retry_delay=args.retry_delay,
    )


if __name__ == "__main__":
    raise SystemExit(main())
