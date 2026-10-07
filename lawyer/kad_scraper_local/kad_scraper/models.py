"""Модель строки выгрузки и колоночная схема.

Порядок колонок зафиксирован требованием к выгрузке; дополнительные поля
(судья, дата регистрации, ИНН участников и т.д.) идут после них, чтобы первые
девять колонок всегда совпадали с ожидаемым форматом.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from . import config

#: Колонки в порядке выгрузки. Значение — ключ словаря записи.
COLUMNS: tuple[str, ...] = (
    "Источник информации",
    "Ссылка на источник",
    "Номер дела",
    "Истцы",
    "Ответчики",
    "Третьи лица",
    "Иные лица",
    "Номер дела по инстанции",
    "Сумма иска",
    "Статус",
    # --- дополнительные поля ---
    "Дата регистрации",
    "Категория дела",
    "Судья",
    "Суд",
    "ИНН истцов",
    "ИНН ответчиков",
    "Участники (адреса)",
    "Страница КАД",
)

#: Колонки, которые заполняются только в режиме глубокого обогащения.
DEEP_ONLY_COLUMNS: tuple[str, ...] = (
    "Третьи лица",
    "Иные лица",
)


@dataclass(slots=True)
class CaseRecord:
    """Одна строка картотеки, готовая к выгрузке в Excel."""

    url: str = ""
    case_number: str = ""
    reg_date: str = ""
    reg_datetime: str = ""
    category: str = ""
    judge: str = ""
    court: str = ""
    plaintiffs: list[dict[str, str]] = field(default_factory=list)
    respondents: list[dict[str, str]] = field(default_factory=list)
    third_parties: list[dict[str, str]] = field(default_factory=list)
    other_persons: list[dict[str, str]] = field(default_factory=list)
    instance_level: str = ""
    instance_desc: str = ""
    status_details: str = ""
    case_events: list[dict[str, str]] = field(default_factory=list)
    claim_amount: str = ""
    status: str = ""
    status_url: str = ""
    kad_page: int = 0
    enriched: bool = False

    @property
    def dedup_key(self) -> str:
        """Ключ дедупликации: ссылка надёжнее номера дела (номера бывают дублями)."""
        return self.url or f"{self.case_number}|{self.reg_date}"

    def as_row(self) -> dict[str, Any]:
        """Плоское представление записи — одна строка листа Excel."""
        all_parties = [*self.plaintiffs, *self.respondents]
        return {
            "Источник информации": config.SOURCE_INFO,
            "Ссылка на источник": self.url,
            "Номер дела": self.case_number,
            "Истцы": _names(self.plaintiffs),
            "Ответчики": _names(self.respondents),
            "Третьи лица": _names(self.third_parties),
            "Иные лица": _names(self.other_persons),
            "Номер дела по инстанции": self.instance_level,
            "Сумма иска": self.claim_amount,
            "Статус": self.status,
            "Дата регистрации": self.reg_date,
            "Категория дела": self.category,
            "Судья": self.judge,
            "Суд": self.court,
            "ИНН истцов": _inns(self.plaintiffs),
            "ИНН ответчиков": _inns(self.respondents),
            "Участники (адреса)": _with_addresses(all_parties),
            "Страница КАД": self.kad_page,
        }

    def as_dict(self) -> dict[str, Any]:
        """Полное структурированное представление — для чекпойнта (JSON).

        В отличие от :meth:`as_row` ничего не склеивает, поэтому запись
        восстанавливается из чекпойнта без потерь.
        """
        return {
            "url": self.url,
            "case_number": self.case_number,
            "reg_date": self.reg_date,
            "reg_datetime": self.reg_datetime,
            "category": self.category,
            "judge": self.judge,
            "court": self.court,
            "plaintiffs": [dict(p) for p in self.plaintiffs],
            "respondents": [dict(p) for p in self.respondents],
            "third_parties": [dict(p) for p in self.third_parties],
            "other_persons": [dict(p) for p in self.other_persons],
"instance_level": self.instance_level,
            "instance_desc": self.instance_desc,
            "status_details": self.status_details,
            "case_events": [dict(event) for event in self.case_events],
            "claim_amount": self.claim_amount,
            "status": self.status,
            "status_url": self.status_url,
            "kad_page": self.kad_page,
            "enriched": self.enriched,
        }

    @classmethod
    def from_saved(cls, raw: dict[str, Any]) -> CaseRecord | None:
        """Восстанавливает запись из чекпойнта. ``None``, если запись пустая."""
        url = _str(raw.get("url"))
        case_number = _str(raw.get("case_number"))
        if not case_number and not url:
            return None

        try:
            kad_page = int(raw.get("kad_page") or 0)
        except (TypeError, ValueError):
            kad_page = 0

        return cls(
            url=url,
            case_number=case_number,
            reg_date=_str(raw.get("reg_date")),
            reg_datetime=_str(raw.get("reg_datetime")),
            category=_str(raw.get("category")),
            judge=_str(raw.get("judge")),
            court=_str(raw.get("court")),
            plaintiffs=clean_parties(raw.get("plaintiffs")),
            respondents=clean_parties(raw.get("respondents")),
            third_parties=clean_parties(raw.get("third_parties")),
            other_persons=clean_parties(raw.get("other_persons")),
instance_level=_str(raw.get("instance_level")),
            instance_desc=_str(raw.get("instance_desc")),
            status_details=_str(raw.get("status_details")),
            case_events=_events(raw.get("case_events")),
            claim_amount=_str(raw.get("claim_amount")),
            status=_str(raw.get("status")),
            status_url=_str(raw.get("status_url")),
            kad_page=kad_page,
enriched=bool(raw.get("enriched")),
        )

    @classmethod
    def from_raw(
        cls,
        raw: dict[str, Any],
        *,
        kad_page: int,
    ) -> CaseRecord:
        """Собирает запись из сырого объекта, снятого ``EXTRACT_ROWS_JS``.

        Таблица результатов не содержит ни номера инстанции, ни текста
        актов — они живят только на карточке дела, поэтому в быстром режиме
        статус ограничен тем, что есть в таблице.
        """
        reg_date = _str(raw.get("reg_date"))
        category = _str(raw.get("category"))

        status = _build_status(
            category=category,
            reg_date=reg_date,
            reg_datetime=_str(raw.get("reg_datetime")),
        )

        return cls(
            url=_str(raw.get("url")),
            case_number=_str(raw.get("case_number")),
            reg_date=reg_date,
            reg_datetime=_str(raw.get("reg_datetime")),
            category=category,
            judge=_str(raw.get("judge")),
            court=_str(raw.get("court")),
            plaintiffs=clean_parties(raw.get("plaintiffs")),
            respondents=clean_parties(raw.get("respondents")),
            status=status,
            kad_page=kad_page,
        )


# --------------------------------------------------------------------------- #
# Хелперы
# --------------------------------------------------------------------------- #


def _str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _events(value: Any) -> list[dict[str, str]]:
    """Хронология дела: список проходов по инстанциям с карточки КАД."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        event = {str(key): _str(val) for key, val in item.items()}
        if (
            event.get("instance_number")
            or event.get("court")
            or event.get("result")
            or event.get("next_hearing")
        ):
            out.append(event)
    return out


def latest_event(events: Sequence[dict[str, str]] | None) -> dict[str, str] | None:
    """Последний по времени проход дела — из него собирается статус.

    КАД выводит хронологию от свежей записи к старой, поэтому «статус дела» —
    это первый блок с текстом судебного акта. Блок без акта пропускаем: у
    инстанции может быть только номер (переход дела), и тогда за ним идёт
    запись с самим актом.
    """
    items = [event for event in (events or []) if isinstance(event, dict)]
    for event in items:
        if _str(event.get("result")):
            return event
    return items[0] if items else None


def _status_from_event(event: dict[str, str]) -> str:
    """Статус по одному проходу дела: наименование инстанции и текст её акта.

    Формат — как в карточке КАД: сперва инстанция, затем номер дела и суд,
    через точку — формулировка последнего судебного акта. Проходу без акта
    отвечает оборотом «Рассматривается в …», как в шапке карточки.
    """
    stage = _str(event.get("stage"))
    number = _str(event.get("instance_number"))
    court = _str(event.get("court"))
    result = _str(event.get("result")).rstrip(" .")
    head = " ".join(part for part in (number, court) if part)
    if not result:
        if not head:
            return ""
        phrase = _PENDING_PHRASES.get(stage.lower(), "Рассматривается")
        return f"{phrase}: {head}"
    if stage and head:
        head = f"{stage}: {head}"
    if head and result:
        return f"{head}. {result}."
    if head:
        return f"{head}."
    return f"{result}." if result else ""


def next_hearing(events: Sequence[dict[str, str]] | None) -> str:
    """Назначенное заседание — дополнение к статусу.

    Отдельная функция, а не поле внутри ``_status_from_event``, потому что
    заседание может быть назначено не в той инстанции, чей акт стал статусом.
    Судьбу дела решает верхняя инстанция, а назначенное заседание — это
    обычно первая: апелляция ещё ждёт постановления, в первой уже назначена
    дата. Статус тогда говорит «постановление вынесено», а заседание есть, и
    терять его нельзя.

    Берём из самой свежей записи, где оно есть: хронология отсортирована от
    новых к старым, а перебирать все и склеивать нельзя — по делу бывает
    несколько отменённых дат, и в статусе останется одна.
    """
    for event in events or []:
        if not isinstance(event, dict):
            continue
        value = _str(event.get("next_hearing"))
        if value:
            return value
    return ""


#: Оборот «рассматривается в …» для стадии, по которой дело ещё ждёт акта.
#: Название стадии КАД даёт в именительном («Апелляционная инстанция»), а в
#: предложном падеже читается «в апелляционной», поэтому короткий словарь
#: вместо склонения. Неизвестная стадия остаётся как есть — лучше видеть
#: подпись КАД, чем ничего.
_PENDING_PHRASES: dict[str, str] = {
    "первая инстанция": "Рассматривается в первой инстанции",
    "апелляционная инстанция": "Рассматривается в апелляционной инстанции",
    "кассационная инстанция": "Рассматривается в кассационной инстанции",
    "надзорная инстанция": "Рассматривается в надзорной инстанции",
}


def pending_instance(events: Sequence[dict[str, str]] | None) -> str:
    """Инстанция, в которой дело ещё ждёт судебного акта.

    Хронология отсортирована от свежей записи к старой, поэтому такая инстанция
    стоит *выше* той, чей акт стал статусом. Так выглядит дело, поданное на
    апелляцию: в хронологии есть блок «Апелляционная инстанция» с номером и
    судом, но без судебного акта, и тогда :func:`latest_event` берёт решение
    первой инстанции. Без этого дополнения номер апелляционного дела из
    выгрузки пропадал совсем — а именно он отвечает на вопрос, где дело сейчас.

    Смотрим только записи выше найденного акта. Если акта нет вовсе, статусом
    становится первая запись по :func:`latest_event`, и дописывать её же
    второй раз незачем.
    """
    items = [event for event in (events or []) if isinstance(event, dict)]
    for index, event in enumerate(items):
        if _str(event.get("result")):
            return _pending_phrase(items[:index])
    return ""


def _pending_phrase(candidates: Sequence[dict[str, str]]) -> str:
    """Оборот о первой инстанции без акта: номер дела и суд."""
    for event in candidates:
        head = " ".join(
            part
            for part in (_str(event.get("instance_number")), _str(event.get("court")))
            if part
        )
        if not head:
            continue
        phrase = _PENDING_PHRASES.get(_str(event.get("stage")).lower(), "Рассматривается")
        return f"{phrase}: {head}"
    return ""


def _extend(status: str, extra: str) -> str:
    """Дополнить статус строкой, которой в нём ещё нет.

    Текст заседания переносим дословно, вместе с особенностями набора КАД
    (пробел перед запятой в «09:30 , зал»): это данные суда, а не наш текст,
    и переписывать их — значит исказить.
    """
    if not extra or extra in status:
        return status
    return f"{status} {extra}" if status else extra


def clean_parties(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _str(item.get("name"))
        if not name:
            continue
        out.append(
            {
                "name": name,
                "inn": _str(item.get("inn")),
                "address": _str(item.get("address")),
            }
        )
    return out


def _party_key(name: str) -> str:
    """Нормализованный вид названия для сопоставления сторон.

    КАД по-разному форматирует одно и то же название в таблице результатов и
    на карточке дела: разные кавычки, лишние пробелы, регистр. Сопоставлять
    стороны по точному равенству тогда нельзя — при обогащении карточки одна
    и та же сторона не найдётся и её ИНН потеряется.
    """
    text = name.upper()
    for ch in "\"'«»`\u00a0\u201c\u201d\u201e":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def merge_parties(
    existing: list[dict[str, str]], incoming: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Дополняет список сторон данными карточки, ничего не теряя.

    Зачем нужен именно слияние, а не замена. ИНН есть только в таблице
    результатов: на карточке дела КАД его не показывает. Поэтому прямое
    присваивание ``record.plaintiffs = card["plaintiffs"]`` стирало бы ИНН,
    ради которого выгрузка и собирается.

    Правила:

    * сторона, у которой уже есть ИНН или адрес, сохраняет их — карточка
      может принести только название;
    * недостающее поле дополняется из карточки;
    * стороны, которых не было в таблице, добавляются в конец: карточка
      показывает полный состав, таблица — не всегда.
    """
    merged = [dict(party) for party in existing]
    by_key = {_party_key(party["name"]): party for party in merged}

    for party in incoming:
        key = _party_key(party["name"])
        target = by_key.get(key)
        if target is None:
            fresh = dict(party)
            merged.append(fresh)
            by_key[key] = fresh
            continue
        for field_name in ("name", "inn", "address"):
            if not target.get(field_name) and party.get(field_name):
                target[field_name] = party[field_name]

    return clean_parties(merged)


def _names(parties: list[dict[str, str]]) -> str:
    return "; ".join(p["name"] for p in parties if p.get("name"))


def _inns(parties: list[dict[str, str]]) -> str:
    return "; ".join(p["inn"] for p in parties if p.get("inn"))


def _with_addresses(parties: list[dict[str, str]]) -> str:
    return " | ".join(
        f"{p['name']} ({p['address']})" if p.get("address") else p["name"]
        for p in parties
        if p.get("name")
    )


def _build_status(*, category: str, reg_date: str, reg_datetime: str) -> str:
    """Статус строки в быстром режиме.

    В таблице результатов КАД нет ни даты заседания, ни номера инстанции —
    только дата регистрации дела. Поэтому собираем статус из того, что есть,
    и не выдумываем остальное.
    """
    parts: list[str] = []
    if category:
        parts.append(f"Категория: {category.lower()}")
    if reg_date:
        parts.append(f"дата регистрации: {reg_date}")
    elif reg_datetime:
        parts.append(f"дата регистрации: {reg_datetime}")
    return "; ".join(parts)


def build_deep_status(
    *,
    case_events: Sequence[dict[str, str]] | None = None,
    instance_desc: str = "",
    next_date: str = "",
    duration: str = "",
    category: str = "",
    reg_date: str = "",
    status_details: str = "",
) -> str:
    """Статус строки после открытия карточки дела.

    Приоритет у хронологии («Карточки»): это движение дела по инстанциям с
    текстом последнего акта, а не сводка из шапки страницы. Если хронологии
    нет — падаем на шапку и дату регистрации, лучше так, чем пустая ячейка.

    Инстанции перечисляются в порядке карточки, от свежей к старой: сверху
    та, где дело сейчас ждёт акта (оборот «Рассматривается в …»), ниже —
    инстанция последнего судебного акта; каждая отдельной строкой, как блоки
    в хронологии. Назначенное заседание дописывается в конец: по нему видно,
    что дело ещё не завершено и когда ждать результата.
    """
    hearing = next_hearing(case_events)
    pending = pending_instance(case_events)

    event = latest_event(case_events)
    if event is not None:
        status = _status_from_event(event)
        if status:
            lines = [part for part in (pending, status) if part]
            return _extend("\n".join(lines), hearing)

    parts: list[str] = []
    for value in (status_details, instance_desc):
        if value and value not in parts:
            parts.append(value)
    if next_date:
        parts.append(f"следующее заседание: {next_date}")
    if hearing and hearing not in parts:
        parts.append(hearing)
    if duration:
        parts.append(f"в производстве {duration}")
    if category:
        parts.append(category.lower())
    if reg_date:
        parts.append(f"дата регистрации: {reg_date}")
    return "; ".join(parts)
