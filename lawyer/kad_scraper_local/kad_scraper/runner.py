"""Выполнение одного задания: обход КАД и сохранение результатов.

Вынесено отдельно от CLI и агента, потому что оба они должны делать ровно
одно и то же — собрать данные и вернуть структурированные записи. Разница
только в том, что агент дополнительно отправляет их на сервер.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from . import protocol
from .exporter import CheckpointStore, records_to_excel, suggest_filename
from .models import CaseRecord
from .scraper import KadScraper, Progress

log = logging.getLogger("kad.runner")

ProgressHook = Callable[[Progress], None]


def run_collection(
    *,
    inn: str,
    limit: int | None = None,
    enrich: bool = False,
    headless: bool = False,
    output_dir: Path | str = "output",
    on_progress: ProgressHook | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Собирает дела по ИНН и возвращает готовый отчёт для протокола.

    Прогресс всегда уходит в чекпойнт, даже если сбор прервался капчей или
    блокировкой. Поэтому повторный запуск того же задания дозаполняет
    выгрузку, а не начинает заново.
    """
    started = time.monotonic()
    output = Path(output_dir)
    checkpoint = CheckpointStore(output, inn)

    scraper = KadScraper(
        inn,
        limit=limit,
        headless=headless,
        enrich=enrich,
        checkpoint=checkpoint,
        seed=seed,
    )
    result = scraper.run(on_progress=on_progress)

    records = [record.as_dict() for record in result.records]
    status = protocol.status_for_stop_reason(
        result.stop_reason, ok=bool(result.records) or result.stop_reason == "no_results"
    )

    report = protocol.make_result(
        job_id="local",
        agent="local",
        status=status,
        records=records,
        stop_reason=result.stop_reason,
        pages_visited=result.pages_visited,
        total_cases=result.total_cases,
        captcha_hits=result.captcha_hits,
        warnings=list(result.warnings),
        error=None,
        elapsed=time.monotonic() - started,
    )
    report["result_path"] = _write_excel(records, inn, output)
    return report


def _write_excel(records: list[dict[str, Any]], inn: str, output: Path) -> str:
    """Собирает xlsx из структурированных записей. Ошибка не роняет отчёт."""
    if not records:
        return ""
    try:
        parsed = [r for r in (CaseRecord.from_saved(raw) for raw in records) if r]
        path = output / suggest_filename(inn)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(records_to_excel(parsed, inn=inn))
        log.info("Excel сохранён: %s", path)
        return str(path)
    except (OSError, ValueError) as exc:
        log.warning("Не удалось собрать Excel: %s", exc)
        return ""


def failed_report(
    *, job_id: str, agent: str, error: BaseException, elapsed: float
) -> dict[str, Any]:
    """Отчёт об аварии агента: собранное не потеряно, причина в ``error``."""
    return protocol.make_result(
        job_id=job_id,
        agent=agent,
        status="error",
        records=[],
        stop_reason="error",
        error=f"{type(error).__name__}: {error}",
        elapsed=elapsed,
    )