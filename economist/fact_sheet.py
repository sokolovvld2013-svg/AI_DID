"""Дата актуальности фактических данных из публичной Google-таблицы."""
import json
import logging
import re
import time
from datetime import date
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300
_cache: dict[str, tuple[float, str]] = {}


def _sheet_id(url: str) -> str:
    match = re.search(r"/spreadsheets/d/([^/]+)", url or "")
    return match.group(1) if match else ""


def _gid(url: str) -> str:
    fragment = urlparse(url or "").fragment
    match = re.search(r"(?:^|&)gid=(\d+)", fragment)
    return match.group(1) if match else ""


def _extract_update_date(text: str) -> str:
    """Берет наиболее позднюю дату из первых строк опубликованного листа."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return ""
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return ""

    table = payload.get("table") or {}
    date_columns = {
        index
        for index, column in enumerate(table.get("cols") or [])
        if column.get("type") in ("date", "datetime")
    }
    found: list[date] = []
    for row in (table.get("rows") or [])[:10]:
        cells = row.get("c") or []
        for index in date_columns:
            if index >= len(cells) or not cells[index]:
                continue
            value = str(cells[index].get("v") or "")
            match = re.fullmatch(r"Date\((\d{4}),(\d{1,2}),(\d{1,2})(?:,.*)?\)", value)
            if match:
                try:
                    # В формате Google Visualization месяц начинается с нуля.
                    found.append(date(int(match[1]), int(match[2]) + 1, int(match[3])))
                except ValueError:
                    pass
    return max(found).strftime("%d.%m.%Y") if found else ""


async def get_fact_sheet_update_date(sheet_url: str) -> str:
    """Возвращает дату актуальности данных в формате dd.mm.yyyy."""
    sheet_id = _sheet_id(sheet_url)
    if not sheet_id:
        return ""

    now = time.monotonic()
    cached = _cache.get(sheet_url)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    query_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:json"
    gid = _gid(sheet_url)
    if gid:
        query_url += f"&gid={gid}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(query_url)
            response.raise_for_status()
        value = _extract_update_date(response.text)
    except Exception as exc:
        logger.warning("Не удалось определить дату обновления Google-таблицы: %s", exc)
        value = ""

    _cache[sheet_url] = (now, value)
    return value
