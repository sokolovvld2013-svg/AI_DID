"""Контекст для режима проверки закупочной документации."""

from __future__ import annotations

import re
from typing import Any

from config import CHECK_LLM_CONTEXT_CHARS, PROCUREMENT_CONTEXT_K
from core.prompt_guards import ANTI_HALLUCINATION_RULES
from lawyer.search_utils import core_query_tokens, keyword_score
from lawyer.text_encoding import strip_urls

SECTION_ORDER = (
    ("info_card", "2", "Информационная карта"),
    ("tz", "3", "Техническое задание"),
    ("contract", "4", "Проект договора"),
    ("forms", "5", "Требования к документам"),
    ("nmcd", "6", "Обоснование НМЦД"),
    ("general", "1", "Общие сведения"),
)

CHECK_SYSTEM_PROMPT = f"""Ты — эксперт по закупкам по 223-ФЗ и Положению о закупке заказчика.

{ANTI_HALLUCINATION_RULES}
- Цифры и реквизиты из документации и Положения — только если они есть в переданных фрагментах; иначе не указывай.

Проверяешь закупочную документацию на соответствие:
1) **223-ФЗ** и смежным актам — формулируй нормы закона по знаниям модели **без** номера [N];
2) **Положению о закупке** — только по переданным фрагментам с номерами [N] из блока «Положение о закупке»;
3) **самой документации** — по фрагментам с номерами [N] из блока «Закупочная документация».

Правила:
- Указывай номера [N] для документации и Положения; для 223-ФЗ — статьи/пункты текстом, без [N], и только если уверен в номере статьи.
- Не выдумывай пункты Положения — только переданные фрагменты.
- Отмечай расхождения документации с Положением, нарушения 223-ФЗ, пропуски, противоречия между разделами.
- Цитируй e-mail, URL, ИНН **дословно** (латиница остаётся латиницей).
- Не заменяй латинский e-mail или домен на кириллические «двойники».
- Если Положение не передано — проверь по 223-ФЗ и укажи, что сверка с Положением невозможна.
- Отвечай структурированно, на русском языке, по существу.
- Каждый пункт нумерованного списка — отдельная законченная мысль с новой строки: 1. 2. 3. (не дроби даты и номера статей вроде 3.4 или 18.07.2011).
- Ключевые термины, названия разделов документации, статьи закона и заголовки этапов выделяй **жирным**.
- Не используй markdown-разделители (`---`, `###`, `##`) — только **жирный** текст, списки и эмодзи 📄/📋/🔴/🟡/🟢.
- Не используй заголовки «Общий вывод» и отдельную строку «Замечания:».

Формат отчёта о проверке — строго такой:

📄 Документация: [способ / тип процедуры, со ссылкой на норму если есть]
Предмет: [предмет / лот]
НМЦД: [сумма, если есть в документации; иначе эту строку не пиши]

📋 Вердикт: 🟢 МОЖНО ПУБЛИКОВАТЬ
или
📋 Вердикт: 🔴 ТРЕБУЕТ ОБЯЗАТЕЛЬНОЙ ДОРАБОТКИ
С новой строки — 1–3 предложения: почему такой вердикт.

🔴 Критические замечания (нарушения ФЗ, ПП РФ, Приказов ФАС, Положения о закупке; расхождения между разделами; copy-paste, риск отмены ФАС)
1. Краткий заголовок замечания
Где: пункт / раздел документации
Суть: что в пункте написано
Обоснование: в чём ошибка или неточность, со ссылкой на норму и [N] при опоре на фрагмент

🟡 Важные замечания (риски при исполнении, споры с участниками)
1. Краткий заголовок замечания
Где: пункт / раздел документации
Суть: что в пункте написано
Обоснование: в чём ошибка или неточность, со ссылкой на норму и [N] при опоре на фрагмент

Если в категории замечаний нет — одной строкой: «Не выявлено».
Каждое поле Где / Суть / Обоснование — с новой строки.
Для точечных вопросов по одному разделу отвечай по существу без обязательной структуры отчёта; ключевые термины всё равно выделяй **жирным**."""


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _section_score(question: str, text: str) -> float:
    q_tokens = core_query_tokens(question)
    if not q_tokens:
        return 1.0
    return keyword_score(q_tokens, text.lower())


def _pick_sections(
    sections: dict[str, dict[str, Any]],
    question: str,
) -> list[tuple[str, dict[str, Any], str, str]]:
    """Возвращает (key, section, number, title) для включения в контекст."""
    available: list[tuple[str, dict[str, Any], str, str, float]] = []
    for key, num, title in SECTION_ORDER:
        sec = sections.get(key)
        if not sec or not (sec.get("text") or "").strip():
            continue
        score = _section_score(question, sec["text"])
        available.append((key, sec, num, title, score))

    if not available:
        return []

    q_lower = question.lower()
    is_full_check = bool(
        re.search(r"провер|аудит|соответств|замечан|ошиб|оцен", q_lower)
    )

    if is_full_check or len(available) <= PROCUREMENT_CONTEXT_K:
        return [(a[0], a[1], a[2], a[3]) for a in available]

    available.sort(key=lambda x: x[4], reverse=True)
    return [(a[0], a[1], a[2], a[3]) for a in available[:PROCUREMENT_CONTEXT_K]]


def build_check_context(
    parsed: dict[str, Any],
    question: str,
    *,
    max_context_chars: int | None = None,
    start_id: int = 1,
) -> tuple[str, list[dict[str, Any]]]:
    """Собирает контекст и список источников для LLM (полный текст разделов)."""
    limit = max_context_chars or CHECK_LLM_CONTEXT_CHARS
    sections = parsed.get("sections") or {}
    filename = parsed.get("filename") or "Документация"
    picked = _pick_sections(sections, question)

    if not picked:
        return "", []

    context_parts: list[str] = []
    citations: list[dict[str, Any]] = []
    context_len = 0

    for i, (_key, sec, num, title) in enumerate(picked, start_id):
        raw = strip_urls(sec.get("text") or "").strip()
        if not raw:
            continue
        part = f"[{i}] {filename}, разд. {num} ({title}):\n{raw}"
        if context_len + len(part) > limit:
            remaining = limit - context_len
            if remaining > 200:
                part = _truncate(part, remaining)
                context_parts.append(part)
                context_len += len(part)
                citations.append({
                    "id": i,
                    "filename": filename,
                    "page": f"разд. {num}",
                    "section": title,
                })
            break
        context_parts.append(part)
        context_len += len(part)
        citations.append({
            "id": i,
            "filename": filename,
            "page": f"разд. {num}",
            "section": title,
        })

    return "\n\n".join(context_parts), citations
