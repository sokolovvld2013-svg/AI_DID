"""Динамическая метка проверки договора."""

from __future__ import annotations

import re

from core.history_label import condense, truncate

_KIND_RULES = (
    (r"договор\s+найм", "Договор найма"),
    (r"договор\s+аренд", "Договор аренды"),
    (r"договор\s+поставк", "Договор поставки"),
    (r"договор\s+купли[- ]продаж", "Договор купли-продажи"),
    (r"договор\s+подряд", "Договор подряда"),
    (r"договор\s+оказани\w*\s+услуг", "Договор оказания услуг"),
    (r"договор\s+поручительств", "Договор поручительства"),
    (r"договор\s+займ", "Договор займа"),
    (r"дополнительн\w*\s+соглашени", "Дополнительное соглашение"),
    (r"соглашени", "Соглашение"),
)

_NUMBER_PATTERNS = (
    re.compile(
        r"\bномер\s+(?:договора|контракта|соглашения)\s*[:№]?\s*"
        r"([А-ЯA-ZЁ0-9][А-ЯA-ZЁ0-9./_-]{0,24})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\n)\s*№\s*(?:договора|контракта|соглашения)\s*[:№]?\s*"
        r"([А-ЯA-ZЁ0-9][А-ЯA-ZЁ0-9./_-]{0,24})",
        re.IGNORECASE,
    ),
)


def _contract_text(contract: dict) -> str:
    fragments = contract.get("fragments") or []
    parts = []
    for fragment in fragments:
        if isinstance(fragment, dict):
            parts.append(str(fragment.get("text") or ""))
        else:
            parts.append(str(fragment))
    return "\n".join(parts)


def _kind(text: str, filename: str) -> str:
    source = f"{text[:6000]}\n{filename}".lower()
    for pattern, label in _KIND_RULES:
        if re.search(pattern, source, re.IGNORECASE):
            return label
    return "Договор"


def _contract_number(text: str, filename: str) -> str:
    source = f"{text[:8000]}\n{filename}"
    for pattern in _NUMBER_PATTERNS:
        match = pattern.search(source)
        if match:
            return f"№ {match.group(1).strip('.,;:')}"
    return ""


def build_history_label(contract: dict | None) -> str:
    if not contract:
        return ""
    text = _contract_text(contract)
    filename = str(contract.get("filename") or "")
    return truncate(condense(_kind(text, filename), _contract_number(text, filename)))
