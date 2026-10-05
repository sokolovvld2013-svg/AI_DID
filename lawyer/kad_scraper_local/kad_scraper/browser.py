"""Жизненный цикл браузера Playwright.

Отдельный модуль, чтобы ``scraper.py`` занимался только логикой обхода, а не
настройкой Chromium.
"""

from __future__ import annotations

import logging
import random
from types import TracebackType
from typing import Self

from playwright.sync_api import Browser, BrowserContext, Page, Response, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from . import config
from .stealth import STEALTH_SCRIPT

log = logging.getLogger("kad.browser")

#: Коды, которыми КАД сообщает о слишком частых запросах.
_THROTTLE_CODES = frozenset({403, 429})


class BrowserUnavailableError(RuntimeError):
    """Ни bundled Chromium, ни системный Chrome/Edge не удалось запустить."""


def _shorten_url(url: str, limit: int = 110) -> str:
    """Убирает query-строку — в сообщениях достаточно пути до endpoint'а."""
    trimmed = url.split("?", 1)[0]
    return trimmed if len(trimmed) <= limit else trimmed[: limit - 1] + "…"


def human_delay(rng: random.Random, low: float, high: float) -> float:
    """Случайная пауза в диапазоне ``[low, high]`` + округление до 0.1 с."""
    return round(rng.uniform(low, high), 1)


class BrowserSession:
    """Контекст-менеджер, поднимающий Chromium с настройками stealth.

    По умолчанию ``headless=False``: КАД закрыт Cloudflare и самой проверкой
    PravoCaptcha, headless-браузер там отсеивается заметно чаще.

    Сначала пробуем bundled Chromium (``playwright install chromium``). Если его
    нет — например, установка браузера была недоступна из-за сети — поднимаем
    системный Chrome через ``channel="chrome"``. Это не хуже для задачи: КАД всё
    равно проверяет обычный Chrome, и лишняя установка не требуется.

    Сессия запоминает ответы с кодом 429/403 (см. :attr:`throttles`): КАД
    ограничивает частоту запросов, и это проявляется не страницей-заглушкой, а
    ошибкой на фоновом запросе вроде ``Kad/SearchInstances``.
    """

    def __init__(
        self,
        *,
        headless: bool = False,
        slow_mo: int = 0,
        seed: int | None = None,
        channel: str | None = None,
    ) -> None:
        self._headless = headless
        self._slow_mo = slow_mo
        self._rng = random.Random(seed)
        self._channel = channel

        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

        #: Ответы КАД с кодом 429/403 за текущий сеанс — «url → код».
        self.throttles: dict[str, int] = {}

    # -- свойства ---------------------------------------------------------- #

    @property
    def throttled(self) -> bool:
        """Хотя бы один запрос КАД отклонён из-за частоты обращений."""
        return bool(self.throttles)

    @property
    def throttle_summary(self) -> str:
        """Человекочитаемая сводка по отклонённым запросам."""
        if not self.throttles:
            return ""
        parts = [f"{code} на {url}" for url, code in self.throttles.items()]
        return "; ".join(parts)

    def clear_throttles(self) -> None:
        self.throttles.clear()

    def _note_response(self, response: Response) -> None:
        status = response.status
        if status not in _THROTTLE_CODES:
            return
        url = _shorten_url(response.url)
        self.throttles[url] = status
        log.warning("КАД отклонил запрос (%s): %s", status, url)

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Браузер не запущен: используйте `with BrowserSession() as s:`")
        return self._page

    @property
    def rng(self) -> random.Random:
        return self._rng

    # -- жизненный цикл ---------------------------------------------------- #

    def start(self) -> Self:
        self._playwright = sync_playwright().start()
        self._browser = _launch_chromium(
            self._playwright,
            headless=self._headless,
            slow_mo=self._slow_mo,
            channel=self._channel,
        )
        self._context = self._browser.new_context(
            user_agent=config.USER_AGENT,
            viewport=config.VIEWPORT,
            locale=config.LOCALE,
            timezone_id=config.TIMEZONE,
            device_scale_factor=1,
            has_touch=False,
            is_mobile=False,
            java_script_enabled=True,
            ignore_https_errors=True,
            extra_http_headers={
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        # Скрываем webdriver до загрузки любого документа.
        self._context.add_init_script(STEALTH_SCRIPT)

        self._context.set_default_timeout(config.DEFAULT_TIMEOUT_MS)
        self._context.set_default_navigation_timeout(config.HOME_LOAD_TIMEOUT_MS)

        self._page = self._context.new_page()
        self._page.on("response", self._note_response)
        # КАД сам подгружает кучу трекеров (Google, Yandex, VK, Mail.ru) —
        # блокируем их, чтобы не тратить время и не светить лишние запросы.
        self._page.route(
            "**/*",
            lambda route: (
                route.abort()
                if _is_blocked_resource(route.request.url)
                else route.continue_()
            ),
        )
        return self

    def close(self) -> None:
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception:  # noqa: BLE001 — закрытие не должно ронять скрап
                pass
        self._page = self._context = self._browser = self._playwright = None

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


_BLOCKED_HOSTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "mc.yandex.ru",
    "yandex.ru",
    "vk.com",
    "top-fwz1.mail.ru",
    "supportkad.bot.one",
    "rambler.ru",
)

#: Каналы, которые пробуем по очереди, если bundled Chromium недоступен.
_FALLBACK_CHANNELS = ("chrome", "msedge")

_LAUNCH_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-infobars",
    "--window-size=1600,1000",
    "--lang=ru-RU",
)


def _launch_chromium(
    playwright: object,
    *,
    headless: bool,
    slow_mo: int,
    channel: str | None,
) -> Browser:
    """Запускает Chromium, при неудаче переходя на системный Chrome/Edge.

    ``channel=None`` — bundled Chromium из ``playwright install``. Если его нет
    (нет сети на скачивание, корпоративный прокси), Playwright падает — тогда
    пробуем установленный в системе Chrome, а затем Edge.
    """
    candidates: list[str | None] = [channel] if channel else [None, *_FALLBACK_CHANNELS]
    problems: list[str] = []

    for index, candidate in enumerate(candidates):
        try:
            return playwright.chromium.launch(
                headless=headless,
                slow_mo=slow_mo,
                channel=candidate,
                args=list(_LAUNCH_ARGS),
            )
        except PlaywrightError as exc:
            label = f"channel={candidate}" if candidate else "bundled Chromium"
            problems.append(f"{label}: {str(exc).splitlines()[0]}")
            log.warning(
                "Не удалось запустить браузер (%s)%s",
                label,
                " — пробуем следующий вариант" if index + 1 < len(candidates) else "",
            )

    raise BrowserUnavailableError(
        "Не удалось запустить браузер Playwright.\n"
        "Варианты, которые были проверены:\n  - " + "\n  - ".join(problems) + "\n\n"
        "Что сделать:\n"
        "  1. Установить браузер Playwright:  playwright install chromium\n"
        "  2. Либо оставить установленный Google Chrome / Microsoft Edge — "
        "парсер подхватит их сам."
    )


def _is_blocked_resource(url: str) -> bool:
    return any(host in url for host in _BLOCKED_HOSTS)
