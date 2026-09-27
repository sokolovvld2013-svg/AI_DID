"""Курс доллара США к рублю по данным ЦБ РФ.

Нужен, чтобы показывать цены DeepSeek (в долларах) в рублях. Курс
запрашивается лениво, кэшируется на несколько часов и не блокирует рендеринг
страницы: при недоступности ЦБ используется последнее известное значение с
пометкой «устарело».
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# Официальный курс ЦБ РФ на 26.09.2026 — запасное значение на случай,
# если сеть недоступна.
FALLBACK_USD_RUB = 84.34
FALLBACK_AS_OF = "26.09.2026"
FALLBACK_URL = "https://www.cbr.ru/scripts/XML_daily.asp"

CACHE_TTL_SECONDS = 6 * 60 * 60
TIMEOUT_SECONDS = 10
USER_AGENT = "Mozilla/5.0 (compatible; assistant/1.0)"

# Сначала официальный XML ЦБ, затем его JSON-зеркало.
CBR_XML_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
CBR_JSON_URL = "https://www.cbr-xml-daily.ru/daily_json.js"

_lock = threading.Lock()
_cache: dict[str, Any] = {}


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", "replace")


def _parse_cbr_xml(body: str) -> tuple[float, str] | None:
    value = re.search(r"CharCode>USD<.*?Value>([\d,\.]+)<", body, re.S)
    date = re.search(r"ValCurs[^>]*Date=\"([\d\.]+)\"", body)
    if not value:
        return None
    try:
        rate = float(value.group(1).replace(",", "."))
    except ValueError:
        return None
    return rate, date.group(1) if date else FALLBACK_AS_OF


def _parse_cbr_json(body: str) -> tuple[float, str] | None:
    import json

    try:
        data = json.loads(body)
    except ValueError:
        return None
    usd = (data.get("Valute") or {}).get("USD")
    if not isinstance(usd, dict):
        return None
    try:
        rate = float(usd["Value"])
    except (KeyError, TypeError, ValueError):
        return None
    raw_date = str(data.get("Date") or "")
    as_of = raw_date[:10].split("T")[0].split("-")[::-1]
    return rate, ".".join(part for part in as_of if part) or FALLBACK_AS_OF


def _fetch_rate() -> tuple[float, str, str] | None:
    for url, parser in ((CBR_XML_URL, _parse_cbr_xml), (CBR_JSON_URL, _parse_cbr_json)):
        try:
            parsed = parser(_fetch(url))
        except Exception as exc:  # сеть недоступна — пробуем следующий источник
            logger.info("Курс ЦБ недоступен (%s): %s", url, exc)
            continue
        if parsed:
            rate, as_of = parsed
            return rate, as_of, url
    return None


def usd_rub(force: bool = False) -> dict[str, Any]:
    """Текущий курс: {rate, as_of, source, url, stale}."""
    now = time.monotonic()
    with _lock:
        cached = _cache.get("value")
        if cached and not force and now - _cache.get("at", 0) < CACHE_TTL_SECONDS:
            return cached

    fetched = _fetch_rate()
    value: dict[str, Any]
    if fetched:
        rate, as_of, url = fetched
        value = {
            "rate": round(rate, 4),
            "as_of": as_of,
            "source": "ЦБ РФ",
            "url": url,
            "stale": False,
        }
    else:
        logger.warning("Курс ЦБ РФ получить не удалось, используется последний известный")
        value = {
            "rate": FALLBACK_USD_RUB,
            "as_of": FALLBACK_AS_OF,
            "source": "ЦБ РФ",
            "url": FALLBACK_URL,
            "stale": True,
        }

    with _lock:
        _cache["at"] = now
        _cache["value"] = value
    return value
