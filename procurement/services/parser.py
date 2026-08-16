"""Разбор единой документации о закупке (DOCX, TXT, PDF) на разделы."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lawyer.doc_processor import load_document

logger = logging.getLogger(__name__)

# Увеличивать при изменении логики извлечения текста (инвалидация disk cache).
PARSE_VERSION = 2

SECTION_KEYS = {
    1: "general",
    2: "info_card",
    3: "tz",
    4: "contract",
    5: "forms",
    6: "nmcd",
}

REQUIRED_SECTIONS = ("info_card", "tz", "contract", "nmcd")

_SECTION_HEADER = re.compile(
    r"(?im)^\s*"
    r"раздел\s*[№#]?\s*"
    r"(?P<num>[1-6])\s*"
    r"(?:[—–\-–.:]\s*)?"
    r"(?P<title>[^\n;]{0,120})?"
)

# Раздел 4 часто без «РАЗДЕЛ № 4», только «ПРОЕКТ ДОГОВОРА»
_CONTRACT_HEADER = re.compile(r"(?im)^\s*проект\s+договора\s*$")

_TITLE_HINTS: list[tuple[str, str]] = [
    ("информационн", "info_card"),
    ("техническ", "tz"),
    ("описание объекта закупки", "tz"),
    ("проект договор", "contract"),
    ("требован", "forms"),
    ("реккомендуем", "forms"),
    ("обоснован", "nmcd"),
    ("начальн", "nmcd"),
    ("нмц", "nmcd"),
    ("общие сведен", "general"),
]


def _pages_to_text(pages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for page in pages:
        text = (page.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _is_valid_section_match(text: str, match: re.Match[str]) -> bool:
    """Отсекает оглавление («…;») и ссылки в скобках («(РАЗДЕЛ № …)»)."""
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.start())
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    if line.rstrip().endswith(";"):
        return False
    before = line[: match.start() - line_start]
    if "(" in before:
        return False
    return True


def _classify_section(num: int, title: str) -> str:
    if num in SECTION_KEYS:
        return SECTION_KEYS[num]
    low = title.lower()
    for hint, key in _TITLE_HINTS:
        if hint in low:
            return key
    return f"section_{num}"


def split_sections(full_text: str) -> dict[str, dict[str, Any]]:
    """Разбивает текст на разделы по заголовкам «РАЗДЕЛ № N» и «ПРОЕКТ ДОГОВОРА»."""
    text = full_text.replace("\r\n", "\n").replace("\r", "\n")
    raw_matches = [
        m for m in _SECTION_HEADER.finditer(text) if _is_valid_section_match(text, m)
    ]
    if not raw_matches:
        return {}

    # Оставляем последнее вхождение каждого номера раздела (оглавление — выше по тексту)
    by_num: dict[int, re.Match[str]] = {}
    for match in raw_matches:
        by_num[int(match.group("num"))] = match
    matches = sorted(by_num.values(), key=lambda m: m.start())

    sections: dict[str, dict[str, Any]] = {}
    for i, match in enumerate(matches):
        num = int(match.group("num"))
        title = (match.group("title") or "").strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        key = _classify_section(num, title)
        sections[key] = {
            "number": num,
            "title": title or f"Раздел {num}",
            "text": body,
            "chars": len(body),
        }

    if "contract" not in sections:
        contract_match = _CONTRACT_HEADER.search(text)
        if contract_match:
            start = contract_match.start()
            end = len(text)
            for key in ("forms", "nmcd"):
                other = sections.get(key)
                if other:
                    idx = text.find(f"РАЗДЕЛ № {other['number']}", start)
                    if idx == -1:
                        idx = text.lower().find(f"раздел № {other['number']}", start)
                    if idx > start:
                        end = min(end, idx)
            body = text[start:end].strip()
            if body:
                sections["contract"] = {
                    "number": 4,
                    "title": "Проект договора",
                    "text": body,
                    "chars": len(body),
                }

    return sections


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_documentation(path: Path, filename: str | None = None) -> dict[str, Any]:
    """Извлекает текст и разделы из файла документации."""
    name = filename or path.name
    pages = load_document(path)
    full_text = _pages_to_text(pages)
    if not full_text.strip():
        raise ValueError("Документ пуст или не удалось извлечь текст")

    sections = split_sections(full_text)
    detected = sorted(sections.keys())
    missing = [k for k in REQUIRED_SECTIONS if k not in sections]

    return {
        "audit_id": str(uuid.uuid4()),
        "filename": name,
        "file_hash": file_hash(path),
        "parse_version": PARSE_VERSION,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "total_chars": len(full_text),
        "sections": sections,
        "sections_detected": detected,
        "missing_sections": missing,
        "structure_ok": not missing,
    }


def summary_for_client(parsed: dict[str, Any]) -> dict[str, Any]:
    """Краткий ответ API без полного текста разделов."""
    sections = parsed.get("sections") or {}
    return {
        "audit_id": parsed["audit_id"],
        "filename": parsed.get("filename"),
        "file_hash": parsed.get("file_hash"),
        "parsed_at": parsed.get("parsed_at"),
        "total_chars": parsed.get("total_chars"),
        "sections_detected": parsed.get("sections_detected", []),
        "missing_sections": parsed.get("missing_sections", []),
        "structure_ok": parsed.get("structure_ok", False),
        "section_sizes": {k: v.get("chars", 0) for k, v in sections.items()},
    }
