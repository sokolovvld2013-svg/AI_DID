"""Извлечение данных из DOM Картотеки арбитражных дел.

Каждый скрипт — одна стрелочная функция ``() => {...}``, которую Playwright
вызывает через ``page.evaluate``. Всё исполняется за один заход в браузере:
если дёргать элементы по одному через CDP, на 30-50 страницах по 25 строк
набегают минуты чистого ожидания.

Структура таблицы результатов (``#b-cases``), 4 колонки:

==============  ==============================================================
``td.num``      ``div.civil|administrative|bankruptcy`` (дата регистрации) +
                ``a.num_case[href]`` (номер дела и ссылка на карточку)
``td.court``    ``div.judge`` (судья) + безымянный ``div`` (название суда)
``td.plaintiff``    участники-истцы, каждый в своём ``span.js-rollover``
``td.respondent``  участники-ответчики, каждый в своём ``span.js-rollover``
==============  ==============================================================

Внутри ``span.js-rollover`` имя участника лежит текстом, а полные данные
(адрес + ИНН) — в скрытом ``span.js-rolloverHtml``. Часть участников может быть
завёрнута в ``div.more`` (счётчик «ещё N»), но текст всё равно присутствует в
DOM, поэтому раскрывать блок не требуется.

На последней странице пагинации у ``li.rarr`` пропадает вложенный ``<a>`` — это
надёжный признак конца выборки (``next_href === null``).
"""

from __future__ import annotations

import json

from . import config


def _js(value: object) -> str:
    """Сериализует значение Python в литерал JavaScript."""
    return json.dumps(value, ensure_ascii=False)


#: Определение капчи и IP-блокировки КАД. Вставляется в каждый экстрактор,
#: потому что блокировка может прийти на любом этапе — и на странице
#: результатов, и на карточке дела.
_PROBE_JS = f"""
  const __title = (document.title || '').trim();
  const __text = ((document.body && document.body.innerText) || '').slice(0, 600);
  const __haystack = __title + ' ' + __text;
  const __blockMarkers = {_js(config.BLOCK_MARKERS)};
  const __blocked = __blockMarkers.some((m) => __haystack.indexOf(m) !== -1);
  const __captcha = !!document.querySelector({_js(config.SEL_CAPTCHA)});
"""


# --------------------------------------------------------------------------- #
# Общие хелперы (вставляются в тело каждой функции)
# --------------------------------------------------------------------------- #

_HELPERS = r"""
  const clean = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : '');

  /**
   * Разбирает одного участника дела из элемента span.js-rollover.
   * Возвращает {name, inn, address} либо null.
   *
   * Имя берётся из ``<strong>`` внутри скрытого блока, а не из видимого
   * текста: в таблице результатов видимая часть — только название, а на
   * карточке дела к нему приклеен адрес, и наивное ``clean(span)`` давало
   * «ООО "НЕВА" 195277, Россия, г. Санкт-Петербург» вместо названия.
   *
   * ИНН есть только в блоке таблицы результатов — на карточке КАД его не
   * показывает вообще, поэтому пустое значение здесь нормально, его
   * дополняют данные таблицы (см. ``_apply_card``).
   */
  const readParty = (span) => {
    const clone = span.cloneNode(true);
    const hidden = clone.querySelector('.js-rolloverHtml');
    const details = clean(hidden);
    if (hidden) hidden.remove();
    const name = clean(clone) || clean(span.querySelector('strong'));
    if (!name) return null;

    const innMatch = details.match(/ИНН:\s*([0-9][0-9\s-]*)/);
    const inn = innMatch ? innMatch[1].replace(/\D/g, '') : '';

    // details == "<название> <адрес> ИНН: 123..." — убираем хвост и повтор имени.
    const address = details
      .replace(/ИНН:[\s\S]*$/, '')
      .replace(name, '')
      .replace(/[|;·•]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();

    return { name: name, inn: inn, address: address };
  };

  /** Все участники из ячейки: ровно один span.js-rollover на участника. */
  const readParties = (td) => {
    if (!td) return [];
    return Array.from(td.querySelectorAll('span.js-rollover'))
      .map(readParty)
      .filter(Boolean);
  };
"""


# --------------------------------------------------------------------------- #
# Таблица результатов поиска
# --------------------------------------------------------------------------- #

EXTRACT_ROWS_JS = f"""() => {{
  const CATEGORY_LABELS = {_js(config.CATEGORY_LABELS)};
{_HELPERS}
{_PROBE_JS}
  const intOf = (sel) => parseInt(((document.querySelector(sel) || {{}}).value) || '0', 10);
  const rows = [];

  document.querySelectorAll('{config.SEL_RESULTS_TABLE} tbody tr').forEach((tr) => {{
    const numTd = tr.querySelector('td.num');
    if (!numTd) return;

    const link = numTd.querySelector('a.num_case');
    const caseNumber = clean(link);
    if (!caseNumber) return; // служебные строки без номера дела

    // КАД меняет класс категории оформлением: civil / civil_simple /
    // bankruptcy и т.п. Поэтому берём первый дочерний блок с title, а
    // категорию восстанавливаем, отбрасывая суффикс _simple.
    const numBox = numTd.querySelector('div.b-container') || numTd;
    const dateBox = Array.from(numBox.children).find(
      (el) => el !== numBox && el.hasAttribute('title')
    ) || null;

    const regDate = clean(dateBox && dateBox.querySelector('span'));
    const regDateTime = (dateBox && dateBox.getAttribute('title')) || '';
    const categoryKey = dateBox
      ? ((dateBox.className || '').trim().split(/\\s+/)[0] || '').replace(/_simple$/, '')
      : '';
    const category = CATEGORY_LABELS[categoryKey] || categoryKey;

    const courtTd = tr.querySelector('td.court');
    const courtBox = courtTd ? courtTd.querySelector('div.b-container') || courtTd : null;
    const courtEl = courtBox
      ? Array.from(courtBox.children).find((d) => !d.classList.contains('judge'))
      : null;
    const judgeEl = courtTd ? courtTd.querySelector('div.judge') : null;

    rows.push({{
      url: (link && (link.href || link.getAttribute('href'))) || '',
      case_number: caseNumber,
      reg_date: regDate,
      reg_datetime: regDateTime,
      category_key: categoryKey,
      category: category,
      judge: judgeEl ? (judgeEl.getAttribute('title') || clean(judgeEl)) : '',
      court: courtEl ? (courtEl.getAttribute('title') || clean(courtEl)) : '',
      plaintiffs: readParties(tr.querySelector('td.plaintiff')),
      respondents: readParties(tr.querySelector('td.respondent')),
      has_more: !!tr.querySelector('div.more')
    }});
  }});

  const nextAnchor = document.querySelector('#b-footer-pages li.rarr a');
  const noResults = document.querySelector('{config.SEL_NO_RESULTS}');

  return {{
    rows: rows,
    page: intOf('{config.SEL_CURRENT_PAGE}'),
    pages_count: intOf('{config.SEL_PAGES_COUNT}'),
    total_count: intOf('{config.SEL_TOTAL_COUNT}'),
    page_size: intOf('{config.SEL_PAGE_SIZE}'),
    found_text: clean(document.querySelector('{config.SEL_FOUND_TOTAL}')),
    no_results: !!noResults && !noResults.classList.contains('g-hidden'),
    captcha: __captcha,
    blocked: __blocked,
    next_href: nextAnchor ? nextAnchor.getAttribute('href') : null
  }};
}}"""


# --------------------------------------------------------------------------- #
# Карточка дела (/Card/<guid>) — режим глубокого обогащения
# --------------------------------------------------------------------------- #

EXTRACT_CARD_JS = f"""() => {{
{_HELPERS}
{_PROBE_JS}
  const PARTY_CELLS = {_js(config.CARD_PARTY_CELLS)};
  const PARTY_LABELS = {_js(config.CARD_PARTY_LABELS)};
  const table = document.querySelector('{config.CARD_SEL_PARTIES_TABLE}');

  /**
   * Все участники из колонки таблицы.
   *
   * Обход построчный, а не через querySelector: первая строка таблицы — это
   * заголовок колонок, и её ячейки имеют те же классы. Например
   * ``table.querySelector('td.third')`` возвращает ``<td class="third">
   * Третьи лица</td>``, из-за чего тре��ьи лица терялись. Поэтому ячейка
   * пропускается, если это ровно подпись колонки, а данные собираются из
   * всех строк под ней.
   */
  const parties = (key) => {{
    if (!table) return [];
    const label = PARTY_LABELS[key];
    const found = [];
    for (const tr of table.querySelectorAll('tr')) {{
      const selectors = PARTY_CELLS[key].split(',').map((s) => s.trim());
      let cell = selectors.map((selector) => tr.querySelector(selector)).find(Boolean) || null;
      if (!cell && key === 'others') {{
        cell = Array.from(tr.querySelectorAll('td')).find((td) =>
          Array.from(td.classList).some((cls) => /other|others|other-person|other_person/i.test(cls))
        ) || null;
      }}
      if (!cell) continue;
      const isHeader = !cell.querySelector('span.js-rollover') && clean(cell) === label;
      if (isHeader) continue;
      const parsed = readParties(cell);
      if (parsed.length) {{
        found.push(...parsed);
      }} else if (clean(cell)) {{
        // Подпись без разметки rollover — участник всё равно назван.
        found.push({{ name: clean(cell), inn: '', address: '' }});
      }}
    }}
    return found;
  }};

  const out = {{
    instance_level: '',
    instance_desc: '',
    next_date: '',
    duration: '',
    category: '',
    status_details: '',
    case_events: [],
    claim_amount: '',
    plaintiffs: parties('plaintiffs'),
    defendants: parties('defendants'),
    third: parties('third'),
    others: parties('others'),
    captcha: __captcha,
    blocked: __blocked
  }};

  const numEl = document.querySelector('{config.CARD_SEL_CASE_NUMBER}');
  if (numEl) out.instance_level = (numEl.getAttribute('data-instance_level') || '').trim();

  const descEl = document.querySelector('{config.CARD_SEL_INSTANCE_DESC}');
  if (descEl) out.instance_desc = clean(descEl);

  const dateEl = document.querySelector('{config.CARD_SEL_NEXT_DATE}');
  if (dateEl) out.next_date = clean(dateEl);

  const durEl = document.querySelector('{config.CARD_SEL_DURATION}');
  if (durEl) out.duration = clean(durEl);

  const catEl = document.querySelector('{config.CARD_SEL_CATEGORY}');
  if (catEl) out.category = clean(catEl);

  const statusNodes = Array.from(document.querySelectorAll(
    '#b-case-header .b-case-header-desc, #b-case-header .b-case-header-status,' +
    ' #b-case-header .case-status, #b-case-header li.case-status,' +
    ' #b-case-header [class*="status"], #b-case-header li.case-date,' +
    ' #b-case-header li.case-dur'
  ));
  out.status_details = Array.from(new Set(statusNodes.map(clean).filter(Boolean))).join('; ');

  // Сумма исковых требований: в шапке карточки — «Сумма исковых требований:
  // 682 933,74 руб.», а на карточках упрощённого производства — только в
  // событии «Заявление» полной хронологии (тег ``additional-info``), которое
  // КАД доливает после разворачивания (EXPAND_CHRONO_JS). Сначала ищем в
  // шапке, затем в хронологии.
  const AMOUNT_RE = /(?:сумма|цена|размер|стоимость)\\s+(?:исковых\\s+)?(?:требований|иска)\\s*[:\\-]?\\s*([0-9](?:[0-9 \\u00a0.,\\-]*[0-9])?)(\\s*(?:руб(?:лей)?|р)\\.?)?/i;
  const headerEl = document.querySelector('#b-case-header');
  let amountMatch = headerEl ? AMOUNT_RE.exec(headerEl.innerText) : null;
  if (!amountMatch) {{
    const chronoEl = document.querySelector('#chrono_list_content');
    amountMatch = chronoEl ? AMOUNT_RE.exec(chronoEl.innerText) : null;
  }}
  out.claim_amount = amountMatch
    ? (amountMatch[1] + (amountMatch[2] || '')).replace(/\\s+/g, ' ').trim()
    : '';

  // Нужный пользователю статус находится не в шапке, а в хронологии дела
  // (вкладка «Карточки»): там видно движение по всем инстанциям, текст
  // последнего судебного акта и ссылку на сам документ.
  //
  // Разбор строгий, по разметке одного прохода дела
  // (``#chrono_list_content > .js-chrono-item-header``): искать блок «Карточки»
  // по тексту заголовка бессмысленно — такого заголовка на странице нет, есть
  // вкладка-кнопка, а разметка соседних инстанций разная.
  const hrefOf = (el) => (el ? (el.href || el.getAttribute('href') || '') : '');
  const pick = (item, selector) => clean(item.querySelector(selector));
  out.case_events = Array.from(
    document.querySelectorAll('{config.CARD_CHRONO_ITEM}')
  ).map((item) => {{
    const resultBox = item.querySelector('{config.CARD_SEL_EVENT_RESULT}');
    const courtBox = item.querySelector('{config.CARD_SEL_EVENT_COURT}');
    const date = pick(item, '{config.CARD_SEL_EVENT_DATE}');
    return {{
      stage: pick(item, '{config.CARD_SEL_EVENT_STAGE}'),
      date: date,
      instance_number: pick(item, '{config.CARD_SEL_EVENT_NUMBER}'),
      court: clean(courtBox),
      court_url: hrefOf(courtBox && courtBox.querySelector('a[href]')),
      result: clean(resultBox),
      result_url: hrefOf(resultBox && resultBox.querySelector('a[href]')),
      next_hearing: pick(item, '{config.CARD_SEL_EVENT_NEXT}')
    }};
  }}).filter((event) =>
    event.instance_number || event.court || event.result
  );

  return out;
}}"""


# --------------------------------------------------------------------------- #
# Вспомогательные действия
# --------------------------------------------------------------------------- #

#: Клик по «Вперёд». Элемент пагинации физически находится вне вьюпорта
#: (layout с ``overflow: hidden``), поэтому обычный ``locator.click()`` в
#: Playwright падает с "element is outside of the viewport". Инициируем клик из
#: JS — обработчик всё равно срабатывает, т.к. навешен через jQuery
#: ``delegate`` на ``#b-footer-pages #pages a``.
CLICK_NEXT_JS = """() => {
  const a = document.querySelector('#b-footer-pages li.rarr a');
  if (!a) return false;
  a.click();
  return true;
}"""

#: Нажатие кнопки «Найти». У КАД это ``<div>`` с обработчиком по клику.
CLICK_SUBMIT_JS = """() => {
  const b = document.querySelector('#b-form-submit');
  if (!b) return false;
  b.click();
  return true;
}"""

#: Удаляет всплывающие уведомления, перекрывающие форму. Пока такой блок
#: висит поверх страницы, Playwright не может кликнуть по полю ввода ИНН:
#: элемент виден и доступен, но событие уходит в оверлей.
DISMISS_OVERLAYS_JS = f"""() => {{
  const selectors = {_js(list(config.OVERLAY_SELECTORS))};
  const seen = new Set();
  let removed = 0;
  for (const selector of selectors) {{
    document.querySelectorAll(selector).forEach((el) => {{
      const node = el.parentElement ? el.closest('.b-promo_notification,'
        + ' .b-promo_notification-popup_wrapper') || el : el;
      if (seen.has(node)) return;
      seen.add(node);
      node.remove();
      removed += 1;
    }});
  }}
  return removed;
}}"""

#: Ставит фокус в поле ввода ИНН напрямую, без клика мышью: обычный
#: ``locator.click()`` проверяет, куда попадёт событие, и падает, если поверх
#: поля лежит чужой блок. На клавиатурный ввод это не влияет — КАД реагирует
#: именно на него.
FOCUS_PARTICIPANT_JS = f"""() => {{
  const el = document.querySelector({_js(config.SEL_PARTICIPANT_INPUT)});
  if (!el) return false;
  el.focus();
  return document.activeElement === el;
}}"""

#: Долилась ли хронология карточки. На медленном ответе КАД отдаёт страницу с
#: заглушкой «Пожалуйста, подождите» и пустыми блоками актов, а тексты доливает
#: следом — без этой проверки статус собрался бы из одних номеров инстанций.
#: Инстанция без судебного актов считается готовой: элемента ``.b-case-result``
#: у неё нет вовсе, а пустой — значит акт ещё не приехал.
CHRONO_READY_JS = f"""() => {{
  const root = document.querySelector({_js(config.CARD_CHRONO_ROOT)});
  if (!root) return false;
  const text = root.textContent || '';
  if (text.indexOf({_js(config.CARD_CHRONO_PLACEHOLDER)}) !== -1) return false;
  const items = Array.from(root.querySelectorAll({_js(config.CARD_CHRONO_ITEM)}));
  if (!items.length) return false;
  return items.every((item) => {{
    const box = item.querySelector({_js(config.CARD_SEL_EVENT_RESULT)});
    if (!box) return true;
    return !!(box.textContent || '').trim();
  }});
}}"""

#: Раскрывает свёрнутую полную хронологию каждой инстанции. КАД показывает по
#: клику на «Нажмите, чтобы ознакомиться с полной хронологией дела»
#: (``.b-collapse.js-collapse``) блок ``.js-chrono-items-wrapper`` с событиями
#: инстанции; именно там живёт тег ``additional-info`` события «Заявление» с
#: суммой исковых требований — в сводке-шапке её нет. ``.click()`` из JS
#: срабатывает, т.к. обработчик навешен как у пагинации, через jQuery.
EXPAND_CHRONO_JS = f"""() => {{
  const root = document.querySelector({_js(config.CARD_CHRONO_ROOT)});
  if (!root) return 0;
  const buttons = Array.from(root.querySelectorAll(
    '{config.CARD_CHRONO_ITEM} .b-collapse.js-collapse'
  )).filter((btn) => {{
    const header = btn.closest('{config.CARD_CHRONO_ITEM}');
    return !header || !header.classList.contains('b-chrono-item-header-expanded');
  }});
  buttons.forEach((btn) => {{
    if (typeof btn.click === 'function') {{
      btn.click();
    }} else {{
      btn.dispatchEvent(new MouseEvent('click', {{ bubbles: true }}));
    }}
  }});
  return buttons.length;
}}"""

#: Долилась ли полная хронология хотя бы одной инстанции: появился блок
#: ``.js-chrono-items-wrapper`` хотя бы с одним событием. Сумма иска лежит в
#: событии «Заявление» самой старой инстанции, обычно первой, поэтому одной
#: загруженной инстанции достаточно. Не дождались — сумма останется пустой,
#: как и раньше.
CHRONO_EXPANDED_READY_JS = f"""() => {{
  const root = document.querySelector({_js(config.CARD_CHRONO_ROOT)});
  if (!root) return false;
  const wrappers = Array.from(root.querySelectorAll('.js-chrono-items-wrapper'));
  return wrappers.some((w) => w.querySelectorAll('.js-chrono-item, .b-chrono-item').length > 0);
}}"""

#: Диагностика страницы без разбора таблицы: капча или IP-блокировка.
CHECK_PAGE_JS = f"""() => {{
{_PROBE_JS}
  return {{
    captcha: __captcha,
    blocked: __blocked,
    title: __title,
    text: __text.slice(0, 300),
    url: location.href
  }};
}}"""
