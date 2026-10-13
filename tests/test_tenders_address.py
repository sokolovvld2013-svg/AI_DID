"""Тесты адреса объекта в «Истории вопросов» модуля «Торги».

Проверяются чистые функции разбора: в метку попадал обрывок подписи таблицы
(«которых расположен объект недвижимости»), а не адрес. Сеть и Chroma не нужны.
"""

from __future__ import annotations

from core.history_label import find_object_address, first_address
from tenders.services.check_label import build_history_label
from tenders.services.fields import extract_address_hint

# Выписка ЕГРН: значение идёт следующей строкой после подписи «Местоположение».
EGRN_TEXT = (
    "Ранее присвоенный государственный учетный номер  :\n"
    "\n"
    "  Инвентарный номер   23023\n"
    "\n"
    "  Местоположение  :\n"
    "\n"
    "  Ленинградская область  ,   Выборгский район  ,   Селезневское сельское поселение  ,"
    "   пос  .   Отрадное  ,   д  .   б  /  н\n"
    "\n"
    "  Кадастровые номера иных объектов недвижимости  ,   в пределах\n"
    "\n"
    "  которых расположен объект недвижимости  :\n"
    "\n"
    "  данные отсутствуют\n"
)

# Та же выписка, но подпись короче порога в 15 символов — раньше она
# пропускалась и адресом становился обрывок «которых расположен…».
SHORT_LABEL_TEXT = (
    "Местоположение:\n"
    "\n"
    "  Ленинградская область, Выборгский район, пос. Отрадное, д. б/н\n"
    "\n"
    "  Кадастровые номера иных объектов недвижимости, в пределах\n"
    "\n"
    "  которых расположен объект недвижимости:\n"
    "\n"
    "  данные отсутствуют\n"
)


def test_egrn_address_is_value_not_label() -> None:
    hint = extract_address_hint(EGRN_TEXT)
    assert "Местоположение" not in hint
    assert "Отрадное" in hint


def test_short_label_line_still_yields_address() -> None:
    hint = extract_address_hint(SHORT_LABEL_TEXT)
    assert "расположен" not in hint
    assert "Отрадное" in hint


def test_inline_address_after_label() -> None:
    line = "Юридический адрес: 107113, г. Москва, пер. Песочный, д. 2."
    assert extract_address_hint(line) == "107113, г. Москва, пер. Песочный, д. 2"


def test_text_without_address_returns_empty() -> None:
    assert extract_address_hint("Закупается оборудование. Начальная цена 500 000 рублей") == ""
    assert extract_address_hint("") == ""


def test_history_label_contains_egrn_address() -> None:
    egrn = {"text": EGRN_TEXT, "fields": {"address": extract_address_hint(EGRN_TEXT)}}
    label = build_history_label(None, egrn, None)
    assert "Отрадное" in label
    assert "Местоположение" not in label
    assert "расположен" not in label


def test_history_label_uses_auction_document_when_no_egrn() -> None:
    auction = {
        "text": (
            "Нежилое помещение, расположенное по адресу: Санкт-Петербург, пр-кт\n"
            "Невский, д. 139, литера. Б, пом. 1-Н | 78,2\n"
        ),
        "fields": {},
    }
    assert build_history_label(auction, None, None) == (
        "Аукцион, Санкт-Петербург, пр-кт Невский, д. 139"
    )


def test_object_address_survives_line_break() -> None:
    text = "расположенное по адресу: Санкт-Петербург, пр-кт\nНевский, д. 139, литера. Б, пом. 1-Н"
    assert find_object_address(text) == "Санкт-Петербург, пр-кт Невский, д. 139"


def test_address_normalization_keeps_settlement_and_house() -> None:
    raw = (
        "Ленинградская область  ,   Выборгский район  ,   Селезневское сельское поселение  ,"
        "   пос  .   Отрадное  ,   д  .   б  /  н"
    )
    normalized = first_address(raw)
    assert "пос. Отрадное" in normalized
    assert "сельское поселение" not in normalized


def test_dotless_abbreviations_get_dots() -> None:
    # В тексте PDF точки у сокращений часто теряются: «г Тула», «ул Доватора».
    raw = "Тульская область  ,   г Тула  ,   ул Доватора  ,   д   1"
    assert first_address(raw) == "Тульская область, г. Тула, ул. Доватора, д. 1"
