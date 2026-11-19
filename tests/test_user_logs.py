"""Проверки чтения журнала пользователей ``core.user_logs``.

Чистые функции без сети и HTTP: журнал пишется построчно по файлу на день,
а здесь важен порядок выдачи — «свежие сверху» при нескольких файлах.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

import core.user_logs as user_logs

#: Даты считаем от сегодняшнего дня: фиксированные даты со временем выходят
#: за окно days=30, и тесты начинают падать через месяц после написания.
_DAY_OLD = user_logs.now_app().date() - timedelta(days=3)
_DAY_MIDDLE = _DAY_OLD + timedelta(days=1)
_DAY_FRESH = _DAY_OLD + timedelta(days=2)

#: тела записей; timestamp одинаковый для всех, порядок держится на номерах.
ROWS = {
    "30": {"ts": f"{_DAY_OLD.isoformat()}T10:00:00+03:00", "module": "m", "question": "старая-1", "tokens": 0, "status": "ok"},
    "31": {"ts": f"{_DAY_OLD.isoformat()}T11:00:00+03:00", "module": "m", "question": "старая-2", "tokens": 0, "status": "ok"},
    "a1": {"ts": f"{_DAY_MIDDLE.isoformat()}T10:00:00+03:00", "module": "m", "question": "средняя-1", "tokens": 0, "status": "ok"},
    "a2": {"ts": f"{_DAY_MIDDLE.isoformat()}T11:00:00+03:00", "module": "m", "question": "средняя-2", "tokens": 0, "status": "ok"},
    "b1": {"ts": f"{_DAY_FRESH.isoformat()}T10:00:00+03:00", "module": "m", "question": "свежая-1", "tokens": 0, "status": "ok"},
    "b2": {"ts": f"{_DAY_FRESH.isoformat()}T11:00:00+03:00", "module": "m", "question": "свежая-2", "tokens": 0, "status": "ok"},
}


def _write_log(path: Path, keys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for key in keys:
            fh.write(json.dumps(ROWS[key], ensure_ascii=False) + "\n")


@pytest.fixture()
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Читаем журнал из временного каталога, а не из конфигурации."""
    data = tmp_path / "logs" / "user_logs"
    monkeypatch.setattr(user_logs, "USER_LOGS_DIR", data)
    return data


def test_read_logs_orders_newest_first_across_files(log_dir: Path) -> None:
    """«Последние» берутся из свежего файла, а не из старого целиком."""
    _write_log(log_dir / f"{_DAY_OLD}.jsonl", ["30", "31"])
    _write_log(log_dir / f"{_DAY_FRESH}.jsonl", ["b1", "b2"])
    _write_log(log_dir / f"{_DAY_MIDDLE}.jsonl", ["a1", "a2"])

    questions = [row["question"] for row in user_logs.read_logs(limit=10, days=30)]

    assert questions == ["свежая-2", "свежая-1", "средняя-2", "средняя-1", "старая-2", "старая-1"]


def test_read_logs_limit_returns_only_last_rows(log_dir: Path) -> None:
    """Лимит режет хвост, оставляя самые свежие записи."""
    _write_log(log_dir / f"{_DAY_OLD}.jsonl", ["30", "31"])
    _write_log(log_dir / f"{_DAY_FRESH}.jsonl", ["b1", "b2"])

    questions = [row["question"] for row in user_logs.read_logs(limit=3, days=30)]

    assert questions == ["свежая-2", "свежая-1", "старая-2"]


def test_read_logs_no_limit_returns_everything(log_dir: Path) -> None:
    """Выбор «все» в интерфейсе отдаёт все записи окна хранения."""
    _write_log(log_dir / f"{_DAY_MIDDLE}.jsonl", ["a1", "a2"])

    questions = [row["question"] for row in user_logs.read_logs(limit=None, days=30)]

    assert questions == ["средняя-2", "средняя-1"]