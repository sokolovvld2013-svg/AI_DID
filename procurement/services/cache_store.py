"""Кэш разобранной документации на диске."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import PROCUREMENT_CACHE_DIR, PROCUREMENT_CACHE_TTL_HOURS
from procurement.services.parser import PARSE_VERSION

logger = logging.getLogger(__name__)

_INDEX_FILE = PROCUREMENT_CACHE_DIR / "index.json"


def _ensure_cache_dir() -> None:
    PROCUREMENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> dict[str, Any]:
    if not _INDEX_FILE.is_file():
        return {"audits": {}}
    try:
        return json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Не удалось прочитать index.json: %s", e)
        return {"audits": {}}


def _save_index(index: dict[str, Any]) -> None:
    _ensure_cache_dir()
    _INDEX_FILE.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_expired(parsed_at: str | None) -> bool:
    if not parsed_at or PROCUREMENT_CACHE_TTL_HOURS <= 0:
        return False
    try:
        ts = datetime.fromisoformat(parsed_at.replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        return age_h > PROCUREMENT_CACHE_TTL_HOURS
    except ValueError:
        return False


def save_parsed(parsed: dict[str, Any]) -> Path:
    _ensure_cache_dir()
    audit_id = parsed["audit_id"]
    file_hash = parsed.get("file_hash") or audit_id
    path = PROCUREMENT_CACHE_DIR / f"{file_hash}.json"
    path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")

    index = _load_index()
    audits = index.setdefault("audits", {})
    audits[audit_id] = {
        "file_hash": file_hash,
        "filename": parsed.get("filename"),
        "parsed_at": parsed.get("parsed_at"),
        "path": path.name,
    }
    _save_index(index)
    return path


def _cache_valid(data: dict[str, Any] | None) -> bool:
    if not data:
        return False
    if _is_expired(data.get("parsed_at")):
        return False
    return data.get("parse_version") == PARSE_VERSION


def get_by_audit_id(audit_id: str) -> dict[str, Any] | None:
    index = _load_index()
    meta = (index.get("audits") or {}).get(audit_id)
    if not meta:
        return None
    path = PROCUREMENT_CACHE_DIR / meta["path"]
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not _cache_valid(data):
        return None
    return data


def get_by_hash(file_hash: str) -> dict[str, Any] | None:
    path = PROCUREMENT_CACHE_DIR / f"{file_hash}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not _cache_valid(data):
        return None
    return data
