"""Усиление запросов и отбора фрагментов Положения о закупке."""

from __future__ import annotations

from typing import Any, Callable

# Срок подачи заявок на аукцион — Таблица 1 / п. 2.13 Положения
_AUCTION_DEADLINE_MARKERS = (
    "15 календар",
    "не менее 15",
    "таблица 1",
    "пункт 2.13",
    "п. 2.13",
)

_AUCTION_ROW_NEEDLE = "проведении аукциона"
_DEADLINE_NEEDLE = "15 календар"

_TARGET_AUCTION_DEADLINE_QUERY = (
    "Таблица 1 Извещение документация о проведении аукциона "
    "Не менее 15 календарных дней до даты окончания срока подачи заявок пункт 2.13"
)


def _is_auction_deadline_question(question: str) -> bool:
    low = question.lower()
    if "аукцион" not in low:
        return False
    return any(
        w in low
        for w in (
            "срок",
            "заяв",
            "подач",
            "прием",
            "приём",
            "окончан",
            "календар",
            "извещ",
        )
    )


def enrich_policy_query(question: str) -> str:
    """Добавляет термины Положения для типовых вопросов эксперта и проверки."""
    q = question.strip()
    low = q.lower()
    extras: list[str] = []

    if any(w in low for w in ("провер", "аудит", "соответств", "замечан", "ошиб", "оцен")):
        extras.extend(
            [
                "требования положение о закупке 223-ФЗ",
                "информационная карта техническое задание договор НМЦД",
            ]
        )

    if _is_auction_deadline_question(q):
        extras.extend(
            [
                "пункт 2.13",
                "таблица 1",
                "Не менее 15 календарных дней до даты окончания срока подачи заявок",
                "Извещение/документация о проведении аукциона",
                "срок подачи заявок на участие в аукционе",
                "аукцион в электронной форме",
            ]
        )

    if not extras:
        return q

    seen = {q.lower()}
    parts = [q]
    for item in extras:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            parts.append(item)
    return " ".join(parts)


def _hit_uid(hit: dict[str, Any]) -> str:
    return f"{hit.get('file_id')}_{hit.get('chunk_index')}"


def _hit_has_auction_deadline(hit: dict[str, Any]) -> bool:
    text = (hit.get("text") or "").lower()
    return any(m in text for m in _AUCTION_DEADLINE_MARKERS)


def _hit_has_auction_table_row(hit: dict[str, Any]) -> bool:
    text = (hit.get("text") or "").lower()
    return _AUCTION_ROW_NEEDLE in text and _DEADLINE_NEEDLE in text


def prioritize_policy_hits(
    question: str,
    hits: list[dict[str, Any]],
    *,
    search_fn: Callable[[str], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Для вопросов о сроке аукциона поднимает фрагмент Таблицы 1 со строкой про аукцион."""
    if not _is_auction_deadline_question(question):
        return hits

    pool = list(hits or [])
    seen = {_hit_uid(h) for h in pool}

    # Доп. поиск по точной формулировке Таблицы 1 — иначе тонет в разделе 13.
    if search_fn is not None:
        for extra in search_fn(_TARGET_AUCTION_DEADLINE_QUERY) or []:
            uid = _hit_uid(extra)
            if uid not in seen:
                pool.append(extra)
                seen.add(uid)

    exact = [h for h in pool if _hit_has_auction_table_row(h)]
    exact_uids = {_hit_uid(h) for h in exact}
    preferred = [
        h
        for h in pool
        if _hit_uid(h) not in exact_uids and _hit_has_auction_deadline(h)
    ]
    preferred_uids = {_hit_uid(h) for h in preferred}
    skip = exact_uids | preferred_uids
    rest = [h for h in pool if _hit_uid(h) not in skip]

    if exact or preferred:
        return exact + preferred + rest
    return pool
