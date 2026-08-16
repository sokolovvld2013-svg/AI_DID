"""Извлечение полей из текстов торговой документации."""

from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

CADASTRAL_RE = re.compile(r"\b\d{2}:\d{2}:\d{6,7}:\d+\b")
AREA_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|кв\.м|м²|м2|кв\s*м)\b",
    re.IGNORECASE,
)
MONEY_RE = re.compile(
    r"(\d+(?:[ \u00a0.,]\d+)*)\s*(?:руб\.?|рублей|₽)",
    re.IGNORECASE,
)
TERM_RE = re.compile(
    r"(\d+)\s*(лет|года|год|мес\.?|месяц(?:ев|а)?)\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b",
)


def normalize_cadastral(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip())


def normalize_text(value: str) -> str:
    s = (value or "").lower().replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def text_similarity(a: str, b: str) -> float:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def find_cadastral_numbers(text: str) -> list[str]:
    return [normalize_cadastral(m.group(0)) for m in CADASTRAL_RE.finditer(text or "")]


def find_areas(text: str) -> list[float]:
    values: list[float] = []
    for m in AREA_RE.finditer(text or ""):
        raw = m.group(1).replace("\u00a0", "").replace(" ", "").replace(",", ".")
        try:
            values.append(float(raw))
        except ValueError:
            continue
    return values


def find_money_values(text: str) -> list[float]:
    values: list[float] = []
    for m in MONEY_RE.finditer(text or ""):
        raw = m.group(1).replace("\u00a0", " ").replace(" ", "").replace(",", ".")
        try:
            values.append(float(raw))
        except ValueError:
            continue
    return values


def find_lease_terms(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for m in TERM_RE.finditer(text or ""):
        unit = m.group(2).lower()
        try:
            out.append((int(m.group(1)), unit))
        except ValueError:
            continue
    return out


def parse_dates(text: str) -> list[datetime]:
    dates: list[datetime] = []
    for d, mo, y in DATE_RE.findall(text or ""):
        year = int(y)
        if year < 100:
            year += 2000
        try:
            dates.append(datetime(year, int(mo), int(d)))
        except ValueError:
            continue
    return dates


def extract_address_hint(text: str) -> str:
    """Грубое извлечение адреса по ключевым словам."""
    lines = (text or "").splitlines()
    for line in lines:
        low = line.lower()
        if any(k in low for k in ("адрес", "местоположен", "расположен")) and len(line.strip()) > 15:
            return line.strip()
    for line in lines:
        if re.search(r"\b(ул\.|пр\.|пер\.|г\.|обл\.|район)\b", line, re.I):
            return line.strip()
    return ""


def extract_owner_hint(text: str) -> str:
    for line in (text or "").splitlines():
        low = line.lower()
        if "собственник" in low or "правообладатель" in low:
            return line.strip()
        if any(k in low for k in ("росимущество", "фгуп", "муницип", "российск")) and len(line) > 20:
            return line.strip()
    return ""


def section_has_keywords(text: str, keywords: tuple[str, ...]) -> bool:
    low = normalize_text(text)
    return any(k in low for k in keywords)


REQUIRED_AUCTION_SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("информация об объекте", ("информация об объекте", "объект аренды", "объект недвижим")),
    ("начальная цена", ("начальная цена", "начальная (минимальная) цена", "цена договора")),
    ("срок аренды", ("срок аренды", "срок действия договора", "срок аренды объекта")),
    ("требования к участникам", ("требования к участникам", "требования к претендентам")),
    ("порядок подачи заявок", ("порядок подачи заявок", "порядок подачи заявки")),
    ("критерии оценки", ("критерии оценки", "критерии определения победителя")),
    ("проект договора", ("проект договора", "договор аренды")),
    ("место проведения аукциона", ("место проведения", "место аукциона")),
    ("дата проведения аукциона", ("дата проведения", "дата аукциона", "время проведения")),
]

CONTRACT_CLAUSES: list[tuple[str, tuple[str, ...]]] = [
    ("предмет договора", ("предмет договора", "предмет настоящего договора")),
    ("срок действия", ("срок действия", "срок аренды")),
    ("размер арендной платы", ("арендная плата", "размер арендной платы", "арендных платеж")),
    ("порядок расчетов", ("порядок расчет", "порядок внесения", "платеж")),
    ("права и обязанности", ("права и обязанности", "обязанности сторон")),
    ("ответственность сторон", ("ответственность сторон", "ответственность за нарушение")),
    ("изменение и расторжение", ("изменение договора", "расторжен", "прекращен")),
    ("заключительные положения", ("заключительные положения", "заключительн")),
]


def extract_auction_fields(text: str) -> dict[str, Any]:
    cadastral = find_cadastral_numbers(text)
    return {
        "cadastral_numbers": cadastral,
        "cadastral": cadastral[0] if cadastral else "",
        "address": extract_address_hint(text),
        "areas": find_areas(text),
        "area": find_areas(text)[0] if find_areas(text) else None,
        "prices": find_money_values(text),
        "lease_terms": find_lease_terms(text),
    }


def extract_egrn_fields(text: str) -> dict[str, Any]:
    cadastral = find_cadastral_numbers(text)
    dates = parse_dates(text)
    return {
        "cadastral_numbers": cadastral,
        "cadastral": cadastral[0] if cadastral else "",
        "address": extract_address_hint(text),
        "areas": find_areas(text),
        "area": find_areas(text)[0] if find_areas(text) else None,
        "owner": extract_owner_hint(text),
        "extract_dates": [d.isoformat() for d in dates],
        "latest_date": max(dates).date().isoformat() if dates else "",
    }


def extract_approval_fields(text: str) -> dict[str, Any]:
    cadastral = find_cadastral_numbers(text)
    dates = parse_dates(text)
    return {
        "cadastral_numbers": cadastral,
        "cadastral": cadastral[0] if cadastral else "",
        "address": extract_address_hint(text),
        "approval_dates": [d.isoformat() for d in dates],
        "latest_date": max(dates).date().isoformat() if dates else "",
        "has_signature_hint": any(
            k in normalize_text(text)
            for k in ("подпис", "директор", "руководител", "утвержда", "согласован")
        ),
    }
