"""Состояние сессии: загруженный договор для проверки (модуль Юрист)."""

from __future__ import annotations

from typing import Any

_contract_by_session: dict[str, dict[str, Any]] = {}


def set_contract(
    session_id: str,
    *,
    filename: str,
    total_chars: int,
    pages: int,
    fragments: list[dict[str, Any]],
) -> None:
    _contract_by_session[session_id] = {
        "filename": filename,
        "total_chars": total_chars,
        "pages": pages,
        "fragments": fragments,
    }


def get_contract(session_id: str) -> dict[str, Any] | None:
    return _contract_by_session.get(session_id)


def clear_contract(session_id: str) -> None:
    _contract_by_session.pop(session_id, None)