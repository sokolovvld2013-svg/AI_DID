"""Скрапер Картотеки арбитражных дел: поиск + умная пагинация.

Как устроен обход
-----------------
1. Открываем ``https://kad.arbitr.ru/``, вводим ИНН в поле «Участник дела».
2. Ждём, пока сайт сам снимет с кнопки «Найти» класс ``b-form-submit_noactive``
   (пока он есть, клик намеренно игнорируется скриптом КАД).
3. Нажимаем «Найти» и ждём отрисовки таблицы результатов.
4. Снимаем все строки текущей страницы одним ``page.evaluate``.
5. Если лимит не достигнут и есть кнопка «Вперёд» — сохраняем чекпойнт,
   делаем случайную паузу 5-10 с, кликаем «Вперёд» и ждём смены
   ``#documentsPage``.

Две особенности КАД, которые здесь учтены и без которых парсер теряет данные:

* **Пагинация не прокручивается.** Кнопка «Вперёд» находится вне вьюпорта
  (layout с ``overflow: hidden``), поэтому ``locator.click()`` в Playwright
  падает с *element is outside of the viewport*. Клик инициируется из JS.
* **Окно страниц «скользящее».** Ссылки на дальние страницы в DOM отсутствуют,
  на странице 3 нет ``#page32``. Перепрыгнуть нельзя — только последовательно
  кликать «Вперёд».
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from . import config
from .browser import BrowserSession, human_delay
from .exporter import CheckpointStore
from .extract import (
    CHECK_PAGE_JS,
    CHRONO_EXPANDED_READY_JS,
    CHRONO_READY_JS,
    CLICK_NEXT_JS,
    CLICK_SUBMIT_JS,
    DISMISS_OVERLAYS_JS,
    EXPAND_CHRONO_JS,
    EXTRACT_CARD_JS,
    EXTRACT_ROWS_JS,
    FOCUS_PARTICIPANT_JS,
)
from .models import (
    CaseRecord,
    build_deep_status,
    clean_parties,
    latest_event,
    merge_parties,
)

log = logging.getLogger("kad.scraper")

#: Снимок страницы, когда таблица не разобралась: пусто, но с ключами.
_EMPTY_SNAPSHOT: dict[str, Any] = {
    "rows": [],
    "page": 0,
    "pages_count": 0,
    "total_count": 0,
    "next_href": None,
    "no_results": False,
    "captcha": False,
    "blocked": False,
}


# --------------------------------------------------------------------------- #
# Прогресс
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Progress:
    """Снимок состояния обхода для интерфейса."""

    phase: str = "Инициализация"
    page: int = 0
    total_pages: int = 0
    collected: int = 0
    total_cases: int = 0
    limit: int | None = None
    message: str = ""
    eta_seconds: float | None = None
    enriched: int = 0
    enrich_total: int = 0
    captcha_hits: int = 0
    warnings: list[str] = field(default_factory=list)


ProgressCallback = Callable[[Progress], None]


# --------------------------------------------------------------------------- #
# Результат
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ScrapeResult:
    records: list[CaseRecord] = field(default_factory=list)
    pages_visited: int = 0
    total_cases: int = 0
    warnings: list[str] = field(default_factory=list)
    finished: bool = False
    captcha_hits: int = 0
    #: Почему обход остановился: ``last_page``, ``limit``, ``no_results``,
    #: ``captcha``, ``blocked``, ``page_failed``, ``max_pages`` или ``error``.
    stop_reason: str = ""

    @property
    def is_partial(self) -> bool:
        """Выгрузка неполная: дошли не до конца выборки."""
        return not self.finished


# --------------------------------------------------------------------------- #
# Скрапер
# --------------------------------------------------------------------------- #


class KadScraper:
    """Обход картотеки по ИНН с постраничным сбором результатов.

    Параметры
    ----------
    inn:
        ИНН или ОГРН контрагента.
    limit:
        Максимум записей; ``None`` — собирать все.
    headless:
        ``False`` (по умолчанию) — видимый браузер, иначе КАД чаще режет
        запросы.
    enrich:
        Дополнительно открывать карточку каждого дела ради «Третьих лиц»,
        «Иных лиц», номера инстанции и даты заседания. Медленно: одна карточка
        ≈ 3-6 с паузы плюс загрузка страницы.
    """

    def __init__(
        self,
        inn: str,
        *,
        limit: int | None = 10,
        headless: bool = False,
        enrich: bool = False,
        delay_range: tuple[float, float] | None = None,
        max_pages: int = config.MAX_PAGES,
        seed: int | None = None,
        checkpoint: CheckpointStore | None = None,
    ) -> None:
        self.inn = _clean_inn(inn)
        self.limit = limit
        self.headless = headless
        self.enrich = enrich
        self.delay_range = delay_range or config.PAGE_DELAY_RANGE
        self.max_pages = max_pages
        self.seed = seed
        self.checkpoint = checkpoint
        self._rng = random.Random(seed)
        self._session: BrowserSession | None = None

    # -- основной вход ----------------------------------------------------- #

    def run(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        initial_records: Iterable[CaseRecord] = (),
    ) -> ScrapeResult:
        report = on_progress or (lambda _: None)

        records: list[CaseRecord] = list(initial_records)
        seen: set[str] = {r.dedup_key for r in records}
        warnings: list[str] = []
        captcha_hits = 0
        pages_visited = 0
        total_cases = 0
        total_pages = 0

        progress = Progress(collected=len(records), limit=self.limit)

        with BrowserSession(headless=self.headless, seed=self.seed) as session:
            self._session = session
            page = session.page
            rng = session.rng

            # --- Шаг 1: главная страница ---------------------------------- #
            self._phase(progress, report, "Открываю картотеку")
            self._open_home(page)

            # --- Шаг 2: ввод ИНН и поиск --------------------------------- #
            self._phase(progress, report, f"Ввожу ИНН {self.inn}")
            self._fill_participant(page, self.inn)

            self._phase(progress, report, "Запускаю поиск")
            total_cases, total_pages, captcha_hits, search_stop = self._submit_search(
                page, progress, report, warnings
            )

            if total_cases == 0:
                self._phase(progress, report, "Ничего не найдено")
                report(
                    _replace(
                        progress,
                        phase="Завершено",
                        message="По вашему запросу дел не найдено",
                        total_pages=total_pages,
                        total_cases=total_cases,
                        warnings=list(warnings),
                    )
                )
                return ScrapeResult(
                    records=records,
                    pages_visited=0,
                    total_cases=total_cases,
                    warnings=warnings,
                    finished=search_stop == "no_results",
                    captcha_hits=captcha_hits,
                    stop_reason=search_stop or "no_results",
                )

            # --- Шаг 3: обход страниц ------------------------------------ #
            progress.total_cases = total_cases
            progress.total_pages = total_pages
            progress.limit = self.limit
            page_started = time.monotonic()
            pages_timed: list[float] = []
            stop_reason = ""

            while True:
                # Замер одной страницы: старт обнуляется на каждой итерации,
                # иначе pages_timed копит нарастающий итог и ETA завышается.
                page_started = time.monotonic()

                if self._limit_reached(len(records)):
                    self._phase(progress, report, "Лимит достигнут")
                    stop_reason = "limit"
                    break

                if pages_visited >= self.max_pages:
                    warnings.append(
                        f"Остановлено на {pages_visited}-й странице: "
                        f"сработал предохранитель {self.max_pages}."
                    )
                    self._phase(progress, report, "Сработал предохранитель числа страниц")
                    stop_reason = "max_pages"
                    break

                snapshot = self._read_page(page, progress, report)
                pages_visited += 1

                if snapshot.get("blocked"):
                    self._handle_block(page, progress, report, warnings)
                    stop_reason = "blocked"
                    break

                if snapshot.get("captcha"):
                    captcha_hits += 1
                    if not self._solve_captcha_manually(page, progress, report):
                        warnings.append("КАД показал капчу — обход остановлен.")
                        stop_reason = "captcha"
                        break
                    continue

                # Тот же случай, что и на поиске: таблица не придёт, пока КАД
                # ограничивает частоту. Проверяем счётчик отклонённых запросов.
                if self._session is not None and self._session.throttled:
                    warnings.append(
                        f"КАД отклонил запросы из-за частоты обращений "
                        f"({self._session.throttle_summary}). Сбор прерван, "
                        f"собранное сохранено в чекпойнт."
                    )
                    stop_reason = "throttled"
                    break

                if snapshot.get("no_results") or not snapshot["rows"]:
                    if pages_visited == 1:
                        self._phase(progress, report, "Ничего не найдено")
                        stop_reason = "no_results"
                    else:
                        # Выборка схлопнулась посреди обхода — это не конец
                        # данных, поэтому помечаем результат как частичный.
                        warnings.append(
                            f"На странице {pages_visited} КАД перестал отдавать "
                            f"строки — сбор остановлен, возможно, дел больше нет "
                            f"либо сработала защита."
                        )
                        stop_reason = "page_empty"
                    break

                page_no = int(snapshot.get("page") or pages_visited)
                total_pages = int(snapshot.get("pages_count") or total_pages) or total_pages
                total_cases = int(snapshot.get("total_count") or total_cases) or total_cases

                for raw in snapshot["rows"]:
                    record = CaseRecord.from_raw(raw, kad_page=page_no)
                    key = record.dedup_key
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(record)
                    if self._limit_reached(len(records)):
                        break

                pages_timed.append(time.monotonic() - page_started)
                progress.page = page_no
                progress.total_pages = total_pages
                progress.collected = len(records)
                progress.total_cases = total_cases
                progress.phase = "Сбор данных"
                progress.message = f"Обрабатываю страницу {page_no} из {total_pages}..."
                progress.eta_seconds = _estimate_eta(
                    pages_timed=pages_timed,
                    page_no=page_no,
                    total_pages=total_pages,
                    delay_range=self.delay_range,
                )
                report(progress)

                # Автосейв: пауза между страницами — идеальное время, чтобы
                # сбросить на диск всё, что уже собрано.
                self._save_checkpoint(records, total_cases, total_pages)

                if self._limit_reached(len(records)):
                    self._phase(progress, report, "Лимит достигнут")
                    stop_reason = "limit"
                    break

                # Конец выборки: у «Вперёд» пропадает <a>, либо счётчик страниц
                # совпал с текущей страницей.
                if not snapshot.get("next_href"):
                    self._phase(
                        progress,
                        report,
                        f"Достигнута последняя страница ({page_no})",
                    )
                    stop_reason = "last_page"
                    break
                if total_pages and page_no >= total_pages:
                    self._phase(
                        progress,
                        report,
                        f"Достигнута последняя страница ({page_no} из {total_pages})",
                    )
                    stop_reason = "last_page"
                    break

                # --- Шаг 4: пауза + переход ----------------------------- #
                pause = human_delay(rng, *self.delay_range)
                progress.phase = "Ожидание"
                progress.message = (
                    f"Пауза {pause:.1f} с перед переходом "
                    f"на страницу {page_no + 1}"
                )
                progress.eta_seconds = _estimate_eta(
                    pages_timed=pages_timed,
                    page_no=page_no,
                    total_pages=total_pages,
                    delay_range=self.delay_range,
                )
                report(progress)
                time.sleep(pause)

                if not self._go_next_page(page, page_no, progress, report):
                    warnings.append(
                        f"Не удалось перейти со страницы {page_no} — сбор прерван."
                    )
                    stop_reason = "page_failed"
                    break

            # --- Шаг 5: глубокое обогащение ----------------------------- #
            if self.enrich and records:
                captcha_hits += self._enrich_records(
                    page, records, progress, report, warnings
                )

        progress.phase = "Завершено"
        progress.collected = len(records)
        progress.message = f"Собрано записей: {len(records)}"
        progress.eta_seconds = 0.0
        report(progress)

        return ScrapeResult(
            records=records,
            pages_visited=pages_visited,
            total_cases=total_cases,
            warnings=warnings,
            finished=stop_reason in ("last_page", "limit"),
            captcha_hits=captcha_hits,
            stop_reason=stop_reason,
        )

    def enrich_records(
        self,
        records: Sequence[CaseRecord],
        *,
        on_progress: ProgressCallback | None = None,
        skip_enriched: bool = True,
    ) -> tuple[int, list[str]]:
        """Догружает карточки у уже собранных записей, без нового поиска.

        Нужно, когда таблица результатов не содержит части данных: КАД не
        показывает третьих лиц и иных лиц в списке, поэтому в делах, где
        организация участвует не истцом и не ответчиком, её нет вообще.
        Такие записи можно дообогатить точечно, не пересобирая всю выгрузку.

        Возвращает пару ``(обогащено, предупреждения)``. Прерывания по капче
        и блокировке не считаются ошибкой: уже собранное остаётся в памяти и
        вызывающий может сохранить его в чекпойнт.
        """
        report = on_progress or (lambda _: None)
        warnings: list[str] = []

        pending = [
            record
            for record in records
            if record.url and not (skip_enriched and record.enriched)
        ]
        if not pending:
            return 0, warnings

        progress = Progress(
            collected=len(records),
            enrich_total=len(pending),
            message=f"Обогащаю карточки: {len(pending)}",
        )

        with BrowserSession(headless=self.headless, seed=self.seed) as session:
            self._session = session
            self._enrich_records(session.page, pending, progress, report, warnings)

        return progress.enriched, warnings

    # ------------------------------------------------------------------ #
    # Шаги
    # ------------------------------------------------------------------ #

    def _open_home(self, page: Page) -> None:
        page.goto(config.BASE_URL, wait_until="domcontentloaded")
        self._dismiss_overlays(page)
        page.wait_for_selector(config.SEL_PARTICIPANT_INPUT, state="visible")
        self._dismiss_overlays(page)

    def _dismiss_overlays(self, page: Page) -> int:
        """Убирает всплывающие уведомления, перекрывающие форму.

        КАД показывает поверх поля ввода ИНН промо-блок Яндекса
        (``b-promo_notification``). Сам он такие блоки не убирает — закрывать их
        должен человек, поэтому снимаем их из DOM.
        """
        try:
            return int(page.evaluate(DISMISS_OVERLAYS_JS) or 0)
        except PlaywrightError as exc:
            log.debug("Не удалось убрать всплывающие блоки: %s", exc)
            return 0

    def _focus_participant(self, page: Page, box: Any) -> None:
        """Ставит фокус в поле ввода ИНН в обход проверки попадания клика.

        Клавиатурный ввод от способа фокуса не меняется, а КАД реагирует именно
        на него — смена значения поля не заменяет фокус.
        """
        try:
            box.focus(timeout=config.INPUT_CLICK_TIMEOUT_MS)
            return
        except (PlaywrightTimeout, PlaywrightError) as exc:
            log.debug("Фокус через locator не удался (%s), пробую из JS", exc)

        try:
            focused = page.evaluate(FOCUS_PARTICIPANT_JS)
        except PlaywrightError as exc:
            log.warning("Не удалось сфокусировать поле ввода ИНН: %s", exc)
            return

        if not focused:
            log.warning("Поле ввода ИНН не приняло фокус — ввод может не сработать.")

    def _focus_participant_input(self, page: Page, box: Any) -> None:
        """Кликает по полю ввода ИНН, а при перекрытии оверлеем — фокусирует его.

        Клик по полю может быть невозможен: поверх него лежит промо-блок
        Яндекса, и Playwright падает с «subtree intercepts pointer events»,
        хотя поле видно, доступно и стабильно. Такой блок убираем и пробуем
        снова; если не помогло — просто фокусируем поле напрямую.
        """
        for attempt in range(1, config.INPUT_CLICK_ATTEMPTS + 1):
            try:
                box.click(timeout=config.INPUT_CLICK_TIMEOUT_MS)
                return
            except (PlaywrightTimeout, PlaywrightError) as exc:
                removed = self._dismiss_overlays(page)
                log.info(
                    "Клик по полю участника перекрыт (попытка %s из %s): %s; "
                    "убрано блоков: %s",
                    attempt,
                    config.INPUT_CLICK_ATTEMPTS,
                    str(exc).splitlines()[0],
                    removed,
                )

        self._focus_participant(page, box)

    def _fill_participant(self, page: Page, value: str) -> None:
        """Вводит ИНН так, как это делает человек.

        КАД снимает с textarea класс ``g-ph`` по событию ввода; если заполнить
        поле через ``fill()``, скрипт может не заметить изменение и кнопка
        «Найти» останется неактивной. Поэтому печатаем посимвольно.
        """
        box = page.locator(config.SEL_PARTICIPANT_INPUT).first
        self._dismiss_overlays(page)
        self._focus_participant_input(page, box)

        box.fill("")
        box.type(value, delay=random.uniform(*config.TYPING_INTERVAL) * 1000)
        page.wait_for_timeout(600)

        # Подсказка может перекрыть кнопку — убираем её, если появилась.
        page.evaluate("document.activeElement && document.activeElement.blur()")

        # Ждём, пока КАД перестанет считать форму пустой.
        page.wait_for_function(
            f"() => !document.querySelector('{config.SEL_SUBMIT}')"
            f".classList.contains('{config.SUBMIT_INACTIVE_CLASS}')",
            timeout=config.DEFAULT_TIMEOUT_MS,
        )

    def _submit_search(
        self, page: Page, progress: Progress, report: ProgressCallback, warnings: list[str]
    ) -> tuple[int, int, int, str]:
        """Нажимает «Найти» и дожидается результатов.

        Возвращает ``(всего дел, страниц, капч, причина остановки)``. Причина
        непустая, если выборку получить не удалось — ``blocked``, ``captcha``
        или ``timeout``: вызывающий код обязан это показать, а не рапортовать
        «дел не найдено».
        """
        deadline = time.monotonic() + config.DEFAULT_TIMEOUT_MS / 1000
        captcha_hits = 0
        rng = self._rng
        throttled_at: float | None = None

        while time.monotonic() < deadline:
            page.evaluate(CLICK_SUBMIT_JS)
            time.sleep(random.uniform(*config.SEARCH_SETTLE_RANGE))

            snapshot = self._read_page(page, progress, report, quiet=True)

            if snapshot.get("blocked"):
                self._handle_block(page, progress, report, warnings)
                return 0, 0, captcha_hits, "blocked"

            # КАД умеет ограничивать частоту без страницы-заглушки: главная
            # отдаётся как обычно, а фоновый запрос вроде Kad/SearchInstances
            # приходит с кодом 429. Тогда таблица не появится никогда.
            if self._session is not None and self._session.throttled:
                if throttled_at is None:
                    # 429 на подсказках участника бывает и при успешном поиске,
                    # поэтому даём КАД время и пробуем ещё раз.
                    throttled_at = time.monotonic()
                    self._phase(
                        progress, report, "КАД отклонил запрос, жду и повторяю"
                    )
                    time.sleep(config.THROTTLE_GRACE_SEC)
                    continue

                if time.monotonic() - throttled_at >= config.THROTTLE_GRACE_SEC:
                    warnings.append(
                        f"КАД отклонил запросы из-за частоты обращений "
                        f"({self._session.throttle_summary}). Это ограничение на "
                        f"частоту запросов, а не на сам ИНН: подождите "
                        f"{config.THROTTLE_WAIT_MIN_MIN:.0f}–"
                        f"{config.THROTTLE_WAIT_MIN_MAX:.0f} мин и повторите запуск."
                    )
                    return 0, 0, captcha_hits, "throttled"

            if snapshot.get("captcha"):
                captcha_hits += 1
                if self._solve_captcha_manually(page, progress, report):
                    continue
                warnings.append("КАД показал капчу на поиске — данные не получены.")
                return 0, 0, captcha_hits, "captcha"

            if snapshot.get("no_results"):
                return (
                    0,
                    int(snapshot.get("pages_count") or 0),
                    captcha_hits,
                    "no_results",
                )

            if snapshot.get("total_count") or snapshot["rows"]:
                return (
                    int(snapshot.get("total_count") or 0),
                    int(snapshot.get("pages_count") or 0),
                    captcha_hits,
                    "",
                )

            # Ответ ещё не приехал — ждём и пробуем ещё раз.
            time.sleep(rng.uniform(2.0, 4.0))

        warnings.append(
            "КАД не вернул результаты поиска за отведённое время. "
            "Вероятно, сработала защита от автоматических запросов — попробуйте "
            "позже или не запускайте парсер слишком часто."
        )
        return 0, 0, captcha_hits, "timeout"

    def _read_page(
        self,
        page: Page,
        progress: Progress,
        report: ProgressCallback,
        *,
        quiet: bool = False,
    ) -> dict[str, Any]:
        """Снимает DOM текущей страницы результатов.

        Во время ожидания прогресса не ждёт — снимок либо содержит готовую
        таблицу, либо частичные данные, которые ``_await_page`` перепроверит.
        """
        if not quiet:
            self._phase(progress, report, "Жду таблицу результатов")

        # Капча и блокировка приходят вместо таблицы, поэтому опрашиваем их
        # ДО ожидания селектора: иначе ждём 40 с впустую и получаем пустую
        # выборку вместо внятного сообщения.
        if self._await_table_or_guard(page, config.PAGE_CHANGE_TIMEOUT_MS):
            return page.evaluate(EXTRACT_ROWS_JS) or _EMPTY_SNAPSHOT

        # Селектор не появился и защиты не видно — отдаём пустой снимок,
        # чтобы вызывающий код сам решил, что это конец обхода.
        return page.evaluate(EXTRACT_ROWS_JS) or _EMPTY_SNAPSHOT

    def _await_table_or_guard(self, page: Page, timeout_ms: int) -> bool:
        """Ждёт появления таблицы результатов, но выходит раньше при капче/блоке.

        Возвращает ``True``, если таблица (или любой непустой набор строк)
        появилась, и ``False``, если вместо неё показана защита.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        interval_ms = 1000

        while time.monotonic() < deadline:
            state = page.evaluate(CHECK_PAGE_JS) or {}
            if state.get("blocked") or state.get("captcha"):
                return False
            if page.locator(f"{config.SEL_RESULTS_TABLE} tbody tr").count():
                return True
            if time.monotonic() + interval_ms / 1000 >= deadline:
                break
            page.wait_for_timeout(interval_ms)

        # Последняя проверка: возможно, страница догрузилась ровно на границе.
        return bool(page.locator(f"{config.SEL_RESULTS_TABLE} tbody tr").count())

    def _go_next_page(
        self, page: Page, current_page: int, progress: Progress, report: ProgressCallback
    ) -> bool:
        """Кликает «Вперёд» и ждёт загрузки следующей страницы.

        Возвращает ``False``, если переход не удался — вызывающий код прервёт
        обход, чтобы не получить бесконечный цикл по одной и той же странице.
        """
        self._phase(progress, report, f"Перехожу на страницу {current_page + 1}")

        for attempt in range(1, config.MAX_PAGE_RETRIES + 1):
            try:
                page.evaluate(CLICK_NEXT_JS)
            except PlaywrightError as exc:  # страница перезагрузилась
                log.debug("Клик «Вперёд» не удался (попытка %s): %s", attempt, exc)
                page.wait_for_timeout(1500)
                continue

            if self._await_page(page, current_page):
                return True

            log.info(
                "Страница %s не сменилась после клика «Вперёд» (попытка %s)",
                current_page,
                attempt,
            )
            pause = human_delay(self._rng, *config.RETRY_DELAY_RANGE)
            self._phase(
                progress,
                report,
                f"Страница не переключилась, повтор через {pause:.0f} с",
            )
            time.sleep(pause)

        return False

    def _await_page(self, page: Page, previous_page: int) -> bool:
        """Ждёт, пока ``#documentsPage`` покажет новую страницу."""
        try:
            page.wait_for_function(
                f"""() => {{
                    const raw = (document.querySelector('{config.SEL_CURRENT_PAGE}')
                                 || {{}}).value || '0';
                    const now = parseInt(raw, 10) || 0;
                    return now > 0 && now !== {previous_page};
                }}""",
                timeout=config.PAGE_CHANGE_TIMEOUT_MS,
            )
        except (PlaywrightTimeout, PlaywrightError):
            return False

        # Даём КАД дописать таблицу: счётчик страницы обновляется в том же
        # AJAX-ответе, но отрисовка строк может отставать на кадр.
        try:
            page.wait_for_function(
                f"""() => {{
                    const raw = (document.querySelector('{config.SEL_CURRENT_PAGE}')
                                 || {{}}).value || '0';
                    const rows = document.querySelectorAll(
                        '{config.SEL_RESULTS_TABLE} tbody tr a.num_case'
                    );
                    return rows.length > 0 && parseInt(raw, 10) !== {previous_page};
                }}""",
                timeout=config.PAGE_CHANGE_TIMEOUT_MS,
            )
        except (PlaywrightTimeout, PlaywrightError):
            return False
        return True

    def _handle_block(
        self,
        page: Page,
        progress: Progress,
        report: ProgressCallback,
        warnings: list[str],
    ) -> None:
        """Реагирует на HTTP 429: КАД заблокировал IP из-за частоты запросов.

        Блокировка снимается только по времени, поэтому ждём остывания и
        один раз пробуем продолжить. Если не помогло — честно сообщаем
        пользователю, сколько уже собрано.
        """
        cooldown = int(config.BLOCK_COOLDOWN_SEC)
        warnings.append(
            f"КАД заблокировал доступ по IP (HTTP 429) после {progress.page} страниц. "
            f"Это ограничение на частоту запросов, а не на сам ИНН. "
            f"Подождите {cooldown // 60} мин и запустите парсер снова — ранее "
            f"собранное подхватится из чекпойнта."
        )

        self._phase(
            progress,
            report,
            f"КАД временно заблокировал IP. Ожидание {cooldown} с…",
        )
        progress.warnings = list(warnings)
        report(progress)
        time.sleep(config.BLOCK_COOLDOWN_SEC)

        try:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(
                config.SEL_RESULTS_TABLE, timeout=config.DEFAULT_TIMEOUT_MS
            )
        except (PlaywrightTimeout, PlaywrightError):
            pass

        state = page.evaluate(CHECK_PAGE_JS) or {}
        if state.get("blocked"):
            warnings.append(
                "После ожидания блокировка КАД не снята — сбор прерван. "
                "Собранные данные сохранены в чекпойнт."
            )
            self._phase(progress, report, "Блокировка КАД не снята")
            progress.warnings = list(warnings)
            report(progress)
            return

        warnings.append("Блокировка КАД снята после ожидания — сбор продолжен.")
        progress.warnings = list(warnings)
        self._phase(progress, report, "Блокировка снята, продолжаю сбор")
        report(progress)

    def _solve_captcha_manually(
        self, page: Page, progress: Progress, report: ProgressCallback
    ) -> bool:
        """Ждёт, пока пользователь сам закроет капчу в видимом окне браузера.

        Возвращает ``True``, если после ожидания таблица результатов появилась.
        """
        if self.headless:
            progress.warnings.append(
                "КАД показал капчу, а браузер запущен в headless — "
                "решить её невозможно. Запустите парсер с видимым окном браузера."
            )
            report(progress)
            return False

        progress.phase = "Требуется капча"
        progress.message = (
            f"КАД показал капчу. Решите её в окне браузера "
            f"(до {int(config.CAPTCHA_GRACE_SEC * 3)} с)..."
        )
        report(progress)

        deadline = time.monotonic() + config.CAPTCHA_GRACE_SEC * 3
        while time.monotonic() < deadline:
            page.wait_for_timeout(1500)
            state = self._eval_page(page, CHECK_PAGE_JS) or {}
            if not state.get("captcha"):
                page.wait_for_timeout(2000)
                return True

        progress.warnings.append("Капча не решена за отведённое время.")
        report(progress)
        return False

    # ------------------------------------------------------------------ #
    # Глубокое обогащение
    # ------------------------------------------------------------------ #

    def _enrich_records(
        self,
        page: Page,
        records: list[CaseRecord],
        progress: Progress,
        report: ProgressCallback,
        warnings: list[str],
    ) -> int:
        """Открывает карточку каждого дела и добирает недостающие поля.

        Медленная операция: КАД отдаёт «Третьих лиц», «Иных лиц», номер инстанции
        и дату заседания только на странице ``/Card/<guid>``, а не в таблице
        результатов.
        """
        rng = self._rng
        captcha_hits = 0
        done = 0
        progress.enrich_total = len(records)
        progress.total_pages = records[-1].kad_page if records else 0

        for index, record in enumerate(records, start=1):
            if not record.url:
                continue

            progress.phase = "Обогащение карточек"
            progress.enriched = done
            progress.page = record.kad_page
            progress.message = (
                f"Карточка {index} из {len(records)}: {record.case_number}"
            )
            progress.eta_seconds = _estimate_enrich_eta(
                done=done, total=len(records), delay_range=config.CARD_DELAY_RANGE
            )
            report(progress)

            try:
                self._open_card(page, record.url)

                # Капча и блокировка приходят раньше таблицы сторон, поэтому
                # проверяем их до ``wait_for_selector`` — иначе ожидание
                # таблицы истечёт по таймауту, так и не дав решить капчу.
                state = self._eval_page(page, CHECK_PAGE_JS) or {}
                if state.get("blocked"):
                    warnings.append(
                        "КАД заблокировал доступ по IP во время обогащения — "
                        "оно прервано, собранные данные сохранены в чекпойнт."
                    )
                    break

                if state.get("captcha"):
                    captcha_hits += 1
                    solved = self._solve_captcha_manually(page, progress, report)
                    if not solved:
                        warnings.append(
                            f"{record.case_number}: капча на карточке — данные не дополнены."
                        )
                        break
                    state = self._eval_page(page, CHECK_PAGE_JS) or {}
                    if state.get("blocked"):
                        warnings.append(
                            "КАД заблокировал доступ по IP во время обогащения — "
                            "оно прервано."
                        )
                        break

                page.wait_for_selector(
                    config.CARD_SEL_PARTIES_TABLE, timeout=config.DEFAULT_TIMEOUT_MS
                )
                self._await_chrono(page)
                card = self._eval_page(page, EXTRACT_CARD_JS) or {}
                # Сумма иска есть не в шапке, а в полной хронологии, которую
                # КАД доливает по клику на «Нажмите, чтобы ознакомиться…».
                # Разворачиваем и разбираем карточку ещё раз, только когда в
                # шапке суммы не нашлось.
                if not str(card.get("claim_amount") or "").strip():
                    self._expand_chrono(page)
                    card = self._eval_page(page, EXTRACT_CARD_JS) or {}
            except (PlaywrightTimeout, PlaywrightError) as exc:
                warnings.append(f"{record.case_number}: не удалось открыть карточку ({exc}).")
                continue

            if card.get("blocked"):
                warnings.append(
                    "КАД заблокировал доступ по IP во время обогащения — оно прервано."
                )
                break

            self._apply_card(record, card)
            record.enriched = True
            done += 1
            progress.enriched = done
            time.sleep(human_delay(rng, *config.CARD_DELAY_RANGE))

        progress.enriched = done
        return captcha_hits

    @staticmethod
    def _await_chrono(page: Page) -> None:
        """Ждёт, пока КАД дольёт хронологию карточки.

        На медленном ответе страница приходит с заглушкой «Пожалуйста,
        подождите» и пустыми блоками актов, а тексты доливаются следом. Без
        ожидания статус собрался бы из одного номера инстанции. Не дождались —
        не беда: разбор отдаст то, что есть, статус соберётся из шапки.
        """
        try:
            page.wait_for_function(CHRONO_READY_JS, timeout=config.CHRONO_WAIT_MS)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            log.debug("Хронология карточки не догрузилась: %s", exc)

    @staticmethod
    def _expand_chrono(page: Page) -> None:
        """Раскрывает полную хронологию инстанций ради суммы иска.

        На карточках упрощённого производства КАД прячет сумму исковых
        требований в теге ``additional-info`` события «Заявление» полной
        хронологии; блок доливается отдельным запросом по клику на «Нажмите,
        чтобы ознакомиться со полной хронологией дела». Не развернулось — не
        беда: сумма останется пустой, как было раньше.
        """
        try:
            page.evaluate(EXPAND_CHRONO_JS)
            page.wait_for_function(
                CHRONO_EXPANDED_READY_JS, timeout=config.CHRONO_EXPAND_WAIT_MS
            )
        except (PlaywrightTimeout, PlaywrightError) as exc:
            log.debug("Полная хронология карточки не развернулась: %s", exc)

    @staticmethod
    def _open_card(page: Page, url: str) -> None:
        """Открыть карточку, пережив перенаправление КАД.

        КАД иногда встречает карточку временной страницей, которая тут же
        перенаправляет на саму карточку: ``goto`` успевает пройти до
        ``domcontentloaded`` и падает с «Navigation is interrupted by another
        navigation». Это не повод терять карточку — переходим заново, пока
        навигация не перестанет конкурировать.
        """
        last: BaseException | None = None
        for _ in range(config.CARD_OPEN_RETRIES):
            try:
                page.goto(url, wait_until="domcontentloaded")
                return
            except (PlaywrightTimeout, PlaywrightError) as exc:
                last = exc
                page.wait_for_timeout(config.CARD_OPEN_RETRY_MS)
        raise PlaywrightError(
            f"карточка не открылась за {config.CARD_OPEN_RETRIES} попыток: {last}"
        )

    @staticmethod
    def _eval_page(page: Page, script: str) -> Any:
        """Выполнить скрипт, переждав незавершённый редирект карточки.

        ``goto`` возвращается по ``domcontentloaded``, но КАД может запустить
        перенаправление следом: прилетает «Execution context was destroyed,
        most likely because of a navigation». Новый контекст появится после
        перехода — пробуем ещё раз через паузу.
        """
        last: BaseException | None = None
        for _ in range(config.CARD_OPEN_RETRIES):
            try:
                return page.evaluate(script)
            except PlaywrightError as exc:
                if "Execution context was destroyed" not in str(exc):
                    raise
                last = exc
                page.wait_for_timeout(config.CARD_OPEN_RETRY_MS)
        raise PlaywrightError(
            f"страница не устоялась за {config.CARD_OPEN_RETRIES} попыток: {last}"
        )

    @staticmethod
    def _apply_card(record: CaseRecord, card: dict[str, Any]) -> None:
        record.instance_level = str(card.get("instance_level") or "").strip()
        record.instance_desc = str(card.get("instance_desc") or "").strip()
        record.status_details = str(card.get("status_details") or "").strip()
        record.case_events = [
            {str(key): str(value).strip() for key, value in event.items()}
            for event in (card.get("case_events") or [])
            if isinstance(event, dict)
        ]
        record.claim_amount = str(card.get("claim_amount") or "").strip()
        record.third_parties = merge_parties(
            record.third_parties, clean_parties(card.get("third"))
        )
        record.other_persons = merge_parties(
            record.other_persons, clean_parties(card.get("others"))
        )

        # На карточке состав сторон полнее, чем в таблице, но ИНН там не
        # показывается вообще. Поэтому данные сливаются: карточка дополняет
        # адреса и добавляет стороны, которых не было видно в таблице, а ИНН
        # из таблицы сохраняется.
        record.plaintiffs = merge_parties(
            record.plaintiffs, clean_parties(card.get("plaintiffs"))
        )
        record.respondents = merge_parties(
            record.respondents, clean_parties(card.get("defendants"))
        )

        record.status = build_deep_status(
            case_events=record.case_events,
            instance_desc=record.instance_desc,
            status_details=record.status_details,
            next_date=str(card.get("next_date") or "").strip(),
            duration=str(card.get("duration") or "").strip(),
            category=str(card.get("category") or record.category).strip(),
            reg_date=record.reg_date,
        )

        # Ссылка на документ, из которого взят статус, — как в карточке КАД:
        # в Excel это кликабельная гиперссылка в ячейке «Статус».
        event = latest_event(record.case_events)
        record.status_url = str((event or {}).get("result_url") or "").strip()

        record.enriched = True

    # ------------------------------------------------------------------ #
    # Служебное
    # ------------------------------------------------------------------ #

    def _limit_reached(self, collected: int) -> bool:
        return self.limit is not None and collected >= self.limit

    def _save_checkpoint(
        self, records: list[CaseRecord], total_cases: int, total_pages: int
    ) -> None:
        """Сбрасывает собранное в ``output/``, если чекпойнт подключён."""
        if self.checkpoint is None:
            return
        self.checkpoint.save(
            records,
            {
                "inn": self.inn,
                "limit": self.limit,
                "total_cases": total_cases,
                "total_pages": total_pages,
                "collected": len(records),
                "enrich": self.enrich,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )

    def _phase(
        self, progress: Progress, report: ProgressCallback, message: str
    ) -> None:
        progress.phase = "Работа"
        progress.message = message
        report(progress)


# --------------------------------------------------------------------------- #
# Утилиты
# --------------------------------------------------------------------------- #


def _clean_inn(inn: str) -> str:
    return "".join(ch for ch in str(inn) if ch.isdigit() or ch.isalpha())


def _replace(progress: Progress, **changes: Any) -> Progress:
    for key, value in changes.items():
        setattr(progress, key, value)
    return progress


def _estimate_eta(
    *,
    pages_timed: list[float],
    page_no: int,
    total_pages: int,
    delay_range: tuple[float, float],
) -> float | None:
    """Оценка остатка времени в секундах.

    Учитывает и фактическое время обхода, и планируемые паузы между
    страницами, поэтому оценка не «прыгает» на каждом шаге.
    """
    if not pages_timed or page_no <= 0:
        return None

    remaining_pages = max(total_pages - page_no, 0)
    per_page = sum(pages_timed) / len(pages_timed)
    pause = sum(delay_range) / 2

    return round(remaining_pages * (per_page + pause), 1)


def _estimate_enrich_eta(
    *, done: int, total: int, delay_range: tuple[float, float]
) -> float:
    pause = sum(delay_range) / 2 + 2.5  # пауза + ориентировочная загрузка
    return round(max(total - done, 0) * pause, 1)


def format_duration(seconds: float | None) -> str:
    """Человекочитаемый ETA: «≈ 4 мин 12 с»."""
    if seconds is None:
        return "—"
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"≈ {seconds} с"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"≈ {minutes} мин {sec} с"
    hours, minutes = divmod(minutes, 60)
    return f"≈ {hours} ч {minutes} мин"
