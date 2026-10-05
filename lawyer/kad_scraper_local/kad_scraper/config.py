"""Конфигурация скрапера Картотеки арбитражных дел (КАД).

Все селекторы и таймауты собраны в одном месте, чтобы при редизайне сайта
достаточно было поправить этот файл.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Источник данных
# --------------------------------------------------------------------------- #

BASE_URL = "https://kad.arbitr.ru/"
CARD_URL = "https://kad.arbitr.ru/Card/{case_id}"

#: Значение колонки «Источник информации» в выгрузке.
SOURCE_INFO = BASE_URL

#: Сколько дел КАД отдаёт на одной странице результатов (зашито в его JS).
ROWS_PER_PAGE = 25

# --------------------------------------------------------------------------- #
# Пресеты поиска
# --------------------------------------------------------------------------- #

DEFAULT_INN = "5032034971"

#: Значения выпадающего списка «Лимит строк» в интерфейсе.
LIMIT_CHOICES: tuple[str, ...] = ("10", "50", "Все")

#: Разбор значения selectbox в числовой лимит (``None`` == без ограничения).
LIMIT_VALUES: dict[str, int | None] = {"10": 10, "50": 50, "Все": None}

#: Типы дел КАД (значение CSS-класса в таблице результатов).
CATEGORY_LABELS: dict[str, str] = {
    "civil": "Гражданские",
    "administrative": "Административные",
    "bankruptcy": "Банкротные",
}

# --------------------------------------------------------------------------- #
# Селекторы DOM (актуальная вёрстка КАД)
# --------------------------------------------------------------------------- #

SEL_PARTICIPANT_INPUT = "#sug-participants textarea"
SEL_PARTICIPANT_TAG = "#sug-participants .tag"

#: Кнопка «Найти». Пока на ней стоит класс ``b-form-submit_noactive``,
#: клик игнорируется скриптом сайта (форма пустая).
SEL_SUBMIT = "#b-form-submit"
SUBMIT_INACTIVE_CLASS = "b-form-submit_noactive"

#: Скрытые счётчики, которые КАД проставляет в ответ на каждый запрос страницы.
SEL_TOTAL_COUNT = "#documentsTotalCount"
SEL_PAGES_COUNT = "#documentsPagesCount"
SEL_CURRENT_PAGE = "#documentsPage"
SEL_PAGE_SIZE = "#documentsPageSize"

#: Всплывающие блоки, которые КАД (через Яндекс) показывает поверх формы:
#: они перекрывают поле «Участник дела» и забирают на себя клики, поэтому
#: ``locator.click()`` падает с «subtree intercepts pointer events». Перед
#: вводом ИНН такие элементы удаляются из DOM.
OVERLAY_SELECTORS: tuple[str, ...] = (
    ".b-promo_notification",
    ".b-promo_notification-popup_wrapper",
    ".b-promo_notification-popup",
    ".b-promo_popup",
    ".b-notif",
)

SEL_RESULTS_TABLE = "#b-cases"
SEL_NO_RESULTS = ".b-noResults"
SEL_FOUND_TOTAL = ".b-found-total"
SEL_CAPTCHA = "form#tokenFrom, .pravocaptcha, #pravocaptcha"

#: Признаки того, что КАД заблокировал IP (HTTP 429). Страница выглядит так:
#: «Доступ к сервису ограничен! ... с вашего IP-адреса поступило необычно
#: много запросов. Система защиты решила, что вместо вас действует программа».
BLOCK_MARKERS: tuple[str, ...] = (
    "Доступ заблокирован",
    "Доступ к сервису ограничен",
    "необычно много запросов",
)

#: Как долго ждать блокировку перед тем, как сдаться.
BLOCK_COOLDOWN_SEC: float = 120.0

#: КАД ограничивает частоту запросов и без страницы-заглушки: главная отдаётся
#: как обычно, а фоновый запрос (например ``Kad/SearchInstances``) приходит с
#: кодом 429. 429 на подсказках участника бывает и при успешном поиске, поэтому
#: сначала даём короткую паузу и повторяем, и только потом сдаёмся.
THROTTLE_GRACE_SEC: float = 15.0
THROTTLE_WAIT_MIN_MIN: float = 15.0
THROTTLE_WAIT_MIN_MAX: float = 30.0

#: Кнопка «Вперёд» в пагинации КАД.
#:
#: ВАЖНО: элемент находится за пределами видимой области (layout с
#: ``overflow: hidden``), поэтому обычный ``locator.click()`` в Playwright
#: падает с "element is outside of the viewport". Клик приходится
#: инициировать через JS — обработчик всё равно срабатывает, т.к. он
#: навешен через jQuery ``delegate`` на ``#b-footer-pages #pages a``.
SEL_NEXT_PAGE = "#b-footer-pages li.rarr a"
SEL_PREV_PAGE = "#b-footer-pages li.larr a"
SEL_PAGER_LINK = "#b-footer-pages ul#pages a[href]"

# --- Карточка дела (/Card/<guid>) ----------------------------------------- #

CARD_SEL_CASE_NUMBER = "#b-case-header .js-case-header-case_num"
CARD_SEL_INSTANCE_DESC = "#b-case-header .b-case-header-desc"
CARD_SEL_NEXT_DATE = "#b-case-header li.case-date"
CARD_SEL_DURATION = "#b-case-header li.case-dur"

#: Категория дела лежит в заголовке блока, который является предком
#: ``#b-case-header``, а не его потомком — поэтому селектор без ``#``.
CARD_SEL_CATEGORY = ".b-iblock__header_card"
CARD_SEL_PARTIES_TABLE = "table.b-case-info"

#: Ячейки таблицы участников карточки.
#:
#: ВАЖНО: в ``table.b-case-info`` первая строка — заголовок колонок, и её ячейки
#: несут *те же* классы (``<td class="plaintiffs">Истцы</td>``). Поэтому
#: ``querySelector('td.plaintiffs')`` попадает в подпись, а не в данные, и
#: список участников выходит пустым. Данные лежат в следующих строках
#: (``<td class="plaintiffs first">…``), поэтому разбор идёт построчно
#: с пропуском ячеек-заголовков — см. ``EXTRACT_CARD_JS``.
CARD_PARTY_CELLS: dict[str, str] = {
    "plaintiffs": "td.plaintiffs",
    "defendants": "td.defendants",
    "third": "td.third, td.third-party, td.third_parties",
    "others": "td.others, td.other, td.other-party, td.other-person, td.other_persons",
}

#: Подписи колонок в строке-заголовке таблицы участников.
CARD_PARTY_LABELS: dict[str, str] = {
    "plaintiffs": "Истцы",
    "defendants": "Ответчики",
    "third": "Третьи лица",
    "others": "Иные лица",
}

# --- Хронология дела (вкладка «Карточки») --------------------------------- #
#
# Это источник статуса: здесь видно движение дела по инстанциям, тексты
# судебных актов и ссылку на сам документ. Разметка одна на все инстанции:
# каждый ``.js-chrono-item-header`` — один проход по делу, от самой свежей
# записи к самой старой.

CARD_CHRONO_ROOT = "#chrono_list_content"
CARD_CHRONO_ITEM = "#chrono_list_content .js-chrono-item-header"
CARD_SEL_EVENT_STAGE = ".l-col strong"
CARD_SEL_EVENT_DATE = ".b-reg-date"
CARD_SEL_EVENT_NUMBER = ".b-case-instance-number"
CARD_SEL_EVENT_COURT = ".instantion-name"
CARD_SEL_EVENT_RESULT = ".b-case-result"

#: Назначенное заседание внутри блока инстанции. Разметка: ``<div
#: class="b-instanceAdditional">`` с иконкой календаря и текстом «Следующее
#: заседание: 21.10.2026, 09:30 , зал № 306». Живёт именно в блоке хронологии
#: (``r-col`` прохода по делу), а не в шапке карточки, поэтому и ищется там же.
CARD_SEL_EVENT_NEXT = ".b-instanceAdditional"

#: Текст-заглушка, который КАД показывает, пока хронология ещё грузится.
CARD_CHRONO_PLACEHOLDER = "Пожалуйста, подождите"

# --------------------------------------------------------------------------- #
# Таймауты и паузы
# --------------------------------------------------------------------------- #

#: Базовый таймаут ожиданий Playwright, мс.
DEFAULT_TIMEOUT_MS = 45_000

#: Сколько ждём первичной загрузки главной страницы КАД, мс.
HOME_LOAD_TIMEOUT_MS = 60_000

#: Клик по полю ввода ИНН: либо проходит сразу, либо его перекрыл оверлей, и
#: тогда дольше ждать бессмысленно — блок убираем и пробуем снова, а поле в
#: крайнем случае фокусируем напрямую из JS.
INPUT_CLICK_TIMEOUT_MS = 5_000
INPUT_CLICK_ATTEMPTS = 2

#: Сколько ждём смены номера страницы после клика «Вперёд», мс.
PAGE_CHANGE_TIMEOUT_MS = 40_000

#: Диапазон паузы между переходами по страницам, сек (антибот-защита КАД).
PAGE_DELAY_RANGE: tuple[float, float] = (5.0, 10.0)

#: Диапазон паузы между открытием карточек дел в режиме обогащения, сек.
CARD_DELAY_RANGE: tuple[float, float] = (3.0, 6.0)

#: Пауза после ввода ИНН и нажатия «Найти», сек.
SEARCH_SETTLE_RANGE: tuple[float, float] = (3.0, 5.0)

#: Задержка между символами при вводе ИНН, сек.
TYPING_INTERVAL: tuple[float, float] = (0.08, 0.22)

#: Сколько секунд ждать перед тем, как считать, что КАД показал капчу.
CAPTCHA_GRACE_SEC: float = 8.0

#: Пауза между повторными попытками, если КАД вернул капчу/таймаут.
RETRY_DELAY_RANGE: tuple[float, float] = (20.0, 40.0)

#: Сколько ждём тексты судебных актов в хронологии карточки, мс. На медленном
#: ответе КАД сначала отдаёт карточку с заглушкой «Пожалуйста, подождите», а
#: акты доливает следом — без этого ожидания статус остаётся пустым.
CHRONO_WAIT_MS = 20_000

#: Максимум попыток перезагрузить страницу результатов.
MAX_PAGE_RETRIES = 3

#: Жёсткий предохранитель: максимальное число страниц за один запуск.
MAX_PAGES = 500

# --------------------------------------------------------------------------- #
# Браузер
# --------------------------------------------------------------------------- #

#: Реалистичный User-Agent. КАД (и Cloudflare за ним) режут headless-Chromium,
#: поэтому маскируемся под обычный десктопный Chrome.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

VIEWPORT: dict[str, int] = {"width": 1600, "height": 1000}
LOCALE = "ru-RU"
TIMEZONE = "Europe/Moscow"
