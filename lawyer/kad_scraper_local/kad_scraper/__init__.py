"""Парсер Картотеки арбитражных дел (kad.arbitr.ru) на Playwright + Streamlit.

Быстрый старт::

    pip install -r requirements.txt
    playwright install chromium
    streamlit run app.py

Пример использования без интерфейса::

    from kad_scraper import KadScraper, records_to_excel

    result = KadScraper("5032034971", limit=50).run()
    open("kad.xlsx", "wb").write(records_to_excel(result.records, inn="5032034971"))
"""

from __future__ import annotations

from .browser import BrowserSession, BrowserUnavailableError
from .config import DEFAULT_INN, LIMIT_CHOICES, LIMIT_VALUES, SOURCE_INFO
from .exporter import (
    CheckpointStore,
    records_to_dataframe,
    records_to_excel,
    suggest_filename,
)
from .models import COLUMNS, DEEP_ONLY_COLUMNS, CaseRecord
from .scraper import KadScraper, Progress, ScrapeResult, format_duration

__all__ = [
    "BrowserSession",
    "BrowserUnavailableError",
    "COLUMNS",
    "CaseRecord",
    "CheckpointStore",
    "DEFAULT_INN",
    "DEEP_ONLY_COLUMNS",
    "KadScraper",
    "LIMIT_CHOICES",
    "LIMIT_VALUES",
    "Progress",
    "SOURCE_INFO",
    "ScrapeResult",
    "format_duration",
    "records_to_dataframe",
    "records_to_excel",
    "suggest_filename",
]

__version__ = "1.0.0"
