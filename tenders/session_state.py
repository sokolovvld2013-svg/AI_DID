"""Состояние сессии: три документа проверки торгов."""

from __future__ import annotations

from typing import Any

ZONES = ("auction", "egrn", "approval")

_docs_by_session: dict[str, dict[str, dict[str, Any]]] = {}


def set_document(session_id: str, zone: str, doc_id: str, filename: str) -> None:
    if zone not in ZONES:
        raise ValueError(f"Неизвестная зона: {zone}")
    bucket = _docs_by_session.setdefault(session_id, {})
    bucket[zone] = {"doc_id": doc_id, "filename": filename}


def get_documents(session_id: str) -> dict[str, dict[str, Any]]:
    return dict(_docs_by_session.get(session_id) or {})


def get_document(session_id: str, zone: str) -> dict[str, Any] | None:
    return (_docs_by_session.get(session_id) or {}).get(zone)


def clear_documents(session_id: str) -> None:
    _docs_by_session.pop(session_id, None)


def all_loaded(session_id: str) -> bool:
    docs = _docs_by_session.get(session_id) or {}
    return all(zone in docs for zone in ZONES)
