"""Короткие метки для «Истории вопросов»: суть проверки вместо текста запроса."""

from __future__ import annotations

import re

MAX_LABEL_LEN = 90

_ADDRESS_LEAD_RE = re.compile(
    r"(?:по\s+адресам?\s*[:\s]+|адрес\w*\s*[:\s]+)?"
    r"((?:Российская Федерация|РФ\s*,?\s*)?"
    r"(?:г\.|город|с\.|пос\.|поселок|д\.|село|пгт|ст\.|станица)\s*[^,|;]{2,40}"
    r"(?:,[^,|;]{2,80}){0,6})",
    re.IGNORECASE,
)

_NOISE_PART_RE = re.compile(
    r"вн\.?\s*тер|внутритерриториальн|муниципальн|городск\w*\s+округ|"
    r"территориальн\w*\s+(?:единиц|образован)|^\d{5,6}$|^№|^[а-яё]{0,3}\.?$",
    re.IGNORECASE,
)

_TOKEN_RULES = (
    (r"^улиц\w*\.?\s+", "ул. "),
    (r"^ул\.\s*", "ул. "),
    (r"^переулок\s+", "пер. "),
    (r"^пер\.?\s+", "пер. "),
    (r"^проспект\s+", "пр-т "),
    (r"^просп\.?\s+", "пр-т "),
    (r"^пр-т\s*", "пр-т "),
    (r"^шоссе\s+", "ш. "),
    (r"^бульвар\s+", "б-р "),
    (r"^б-р\s*", "б-р "),
    (r"^набережн\w*\s+", "наб. "),
    (r"^наб\.\s*", "наб. "),
    (r"^площадь\s+", "пл. "),
    (r"^пл\.\s*", "пл. "),
    (r"^проезд\s+", "проезд "),
    (r"^тракт\s+", "тракт "),
    (r"^микрорайон\s+", "мкр. "),
    (r"^мкр\.?\s*", "мкр. "),
    (r"^территория\s+", "тер. "),
    (r"^город\s+", "г. "),
    (r"^г\.\s*", "г. "),
    (r"^пос[её]лок\s+", "с. "),
    (r"^село\s+", "с. "),
    (r"^пгт\s+", "пгт "),
    (r"^станица\s+", "ст. "),
    (r"^ст\.\s*", "ст. "),
    (r"^дом\s*№?\s*", "д. "),
    (r"^д\.\s*", "д. "),
    (r"^д\s+(?=\d)", "д. "),
    (r"^владение\s+", "вл. "),
    (r"^вл\.\s*", "вл. "),
)

_HOUSE_RE = re.compile(r"^(?:д\.|вл\.)\s*\d", re.IGNORECASE)

_PROCEDURE_RULES = (
    (r"котировочн\w*\s+сесси|биржев\w*\s+аукцион", "Котировочная сессия"),
    (r"запрос\w*\s+предложен", "Запрос предложений"),
    (r"запрос\w*\s+цен|запрос\w*\s+котировок", "Запрос цен"),
    (r"аукцион", "Аукцион"),
    (r"конкурс", "Конкурс"),
    (
        r"единственн\w+\s+(?:поставщик|исполнител|подрядчик|участник|подрядчик)",
        "Закупка у единственного поставщика",
    ),
    (r"закупк\w+\s+мал\w*\s+объ[её]м", "Закупка малого объёма"),
    (r"открыт\w*\s+закупк", "Открытая закупка"),
)


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip(" \t\r\n|;,.:")


def truncate(text: str, limit: int = MAX_LABEL_LEN) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;.") + "…"


def condense(*parts: str | None, limit: int = MAX_LABEL_LEN) -> str:
    """Склеивает непустые части через запятую без повторов."""
    chunks: list[str] = []
    seen: set[str] = set()
    for part in parts:
        chunk = clean_text(part)
        key = chunk.lower()
        if not chunk or key in seen:
            continue
        seen.add(key)
        chunks.append(chunk)
    return truncate(", ".join(chunks), limit)


def _norm_part(part: str) -> str:
    out = part.strip(" \t\r\n,.;")
    for pattern, replacement in _TOKEN_RULES:
        new = re.sub(pattern, replacement, out, count=1, flags=re.IGNORECASE)
        if new != out:
            return new
    return out


def _normalize_tokens(text: str) -> str:
    """Строит адрес из частей через запятую, обрывая на номере дома."""
    kept: list[str] = []
    has_house = False
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part or _NOISE_PART_RE.search(part):
            continue
        normalized = _norm_part(part)
        if not normalized or _NOISE_PART_RE.search(normalized):
            continue
        if has_house:
            break
        kept.append(normalized)
        if _HOUSE_RE.match(normalized):
            has_house = True
    return ", ".join(kept)


def normalize_address(raw: str | None) -> str:
    """«Российская Федерация, город Москва, вн.тер.г. м.о. Басманный, улица Нижняя Красносельская, дом 35, строение 2» → «г. Москва, ул. Нижняя Красносельская, д. 35»."""
    text = clean_text(raw).split(";")[0].split("|")[0]
    if not text:
        return ""
    match = _ADDRESS_LEAD_RE.search(text)
    if match:
        text = match.group(1)
    return _normalize_tokens(text)


def first_address(text: str | None) -> str:
    return normalize_address(text)


def find_object_address(text: str | None) -> str:
    """Ищет адрес объекта по маркеру «расположенн... по адресу».

    Если ведущий маркер города отсутствует (например «Санкт-Петербург, ул. Садовая,
    д. 72/16»), первый проход может обрезать адрес до номера дома — тогда
    адрес пересчитывается по захваченной части целиком.
    """
    source = text or ""
    match = re.search(
        r"расположенн\w*\s+(?:по\s+)?адресу\s*[:\s]\s*([^|\n;]{5,220})",
        source,
        re.IGNORECASE,
    )
    if match:
        raw_address = match.group(1)
        address = normalize_address(raw_address)
        if address and not _HOUSE_RE.match(address):
            return address
        address = _normalize_tokens(raw_address)
        if address:
            return address
    return normalize_address(source)


def pick_procedure(text: str | None) -> str:
    """«Открытый конкурс в электронной форме (Конкурс)» → «Конкурс»."""
    source = (text or "").lower()
    if not source.strip():
        return ""
    for pattern, name in _PROCEDURE_RULES:
        if re.search(pattern, source, re.IGNORECASE):
            return name
    return ""


def row_value(table_text: str | None, keys: tuple[str, ...]) -> str:
    """Значение строки «Ключ | Значение» из разобранной таблицы раздела."""
    for line in (table_text or "").splitlines():
        for key in keys:
            if key.lower() not in line.lower():
                continue
            if "|" in line:
                value = line.split("|", 1)[1]
                if value.strip():
                    return clean_text(value)
    return ""
