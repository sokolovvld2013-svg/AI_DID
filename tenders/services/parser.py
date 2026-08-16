"""Разбор документов для модуля «Торги»."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lawyer.doc_processor import load_document
from tenders.services.fields import (
    extract_approval_fields,
    extract_auction_fields,
    extract_egrn_fields,
)

PARSE_VERSION = 1

DOC_LABELS = {
    "auction": "Торговая документация",
    "egrn": "Выписка из ЕГРН",
    "approval": "Согласование сделки",
}


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _pages_to_text(pages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for page in pages:
        text = (page.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def parse_document(path: Path, *, doc_type: str, filename: str | None = None) -> dict[str, Any]:
    name = filename or path.name
    pages = load_document(path)
    text = _pages_to_text(pages)
    if not text.strip():
        raise ValueError(f"Не удалось извлечь текст из файла «{name}»")

    if doc_type == "auction":
        fields = extract_auction_fields(text)
    elif doc_type == "egrn":
        fields = extract_egrn_fields(text)
    elif doc_type == "approval":
        fields = extract_approval_fields(text)
    else:
        raise ValueError(f"Неизвестный тип документа: {doc_type}")

    return {
        "doc_id": str(uuid.uuid4()),
        "doc_type": doc_type,
        "filename": name,
        "file_hash": file_hash(path),
        "parse_version": PARSE_VERSION,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "total_chars": len(text),
        "text": text,
        "fields": fields,
    }


def summary_for_client(parsed: dict[str, Any]) -> dict[str, Any]:
    fields = parsed.get("fields") or {}
    return {
        "doc_id": parsed["doc_id"],
        "doc_type": parsed.get("doc_type"),
        "filename": parsed.get("filename"),
        "file_hash": parsed.get("file_hash"),
        "parsed_at": parsed.get("parsed_at"),
        "total_chars": parsed.get("total_chars"),
        "cadastral": fields.get("cadastral") or "",
    }
