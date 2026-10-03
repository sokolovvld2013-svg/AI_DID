"""Динамическая метка проверки закупочной документации."""

from __future__ import annotations

from core.history_label import (
    condense,
    find_object_address,
    first_address,
    pick_procedure,
    row_value,
)

_PROCEDURE_KEYS = (
    "Способ определения поставщика",
    "Способ закупки",
    "Способ проведения закупки",
    "Вид закупки",
)

_SUBJECT_KEYS = (
    "Предмет закупки",
    "Наименование и описание объекта закупки",
    "Наименование объекта закупки",
)


def build_history_label(parsed: dict | None) -> str:
    info_card = ((parsed or {}).get("sections") or {}).get("info_card") or {}
    text = info_card.get("text") or ""
    procedure = pick_procedure(row_value(text, _PROCEDURE_KEYS))
    subject_value = row_value(text, _SUBJECT_KEYS)
    subject = find_object_address(subject_value) or first_address(subject_value)
    return condense(procedure, subject)
