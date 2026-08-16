"""Состояние сессии: загруженная закупочная документация."""

from __future__ import annotations

from typing import Any

_doc_by_session: dict[str, dict[str, Any]] = {}


def set_documentation(session_id: str, audit_id: str, filename: str) -> None:
    _doc_by_session[session_id] = {
        "audit_id": audit_id,
        "filename": filename,
    }


def get_documentation(session_id: str) -> dict[str, Any] | None:
    return _doc_by_session.get(session_id)


def clear_documentation(session_id: str) -> None:
    _doc_by_session.pop(session_id, None)
