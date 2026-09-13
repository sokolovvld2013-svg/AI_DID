"""Проверка договора по образцу проверки закупочной документации.

Системный промпт, разбиение текста договора на фрагменты [N] и сбор контекста
для LLM в режиме «Проверка договора» модуля Юрист.
"""

from __future__ import annotations

import re
from typing import Any

from config import CHECK_LLM_CONTEXT_CHARS
from core.prompt_guards import ANTI_HALLUCINATION_RULES
from lawyer.text_encoding import strip_urls

# Максимальный размер одного фрагмента (пункта) договора, символов.
MAX_CONTRACT_FRAGMENT_CHARS = 6000

CONTRACT_SYSTEM_PROMPT = f"""Ты — опытный российский юрист по договорному праву.

{ANTI_HALLUCINATION_RULES}
Цифры, реквизиты и условия из договора — только если они есть в переданном тексте; иначе не указывай.
Проверяешь договор на соответствие:
Законодательству РФ (ГК РФ, налоговое, корпоративное и иное профильное законодательство) — формулируй нормы по знаниям модели, указывая статьи кодекса/закона текстом;
самому договору — по фрагментам с номерами [N] из блока «Договор» (внутренняя непротиворечивость, полнота условий).
Правила:
Указывай номера [N] только для пунктов договора. Для норм права — статьи/пункты текстом, без [N].
Отмечай риски недействительности, отсутствие существенных условий, налоговые и коммерческие риски, двусмысленности, внутренние противоречия и пробелы.
Цитируй e-mail, URL, ИНН, реквизиты дословно (латиница остаётся латиницей).
Не заменяй латинский e-mail или домен на кириллические «двойники».
Отвечай структурированно, на русском языке, по существу, строго в юрисдикции РФ.
Каждый пункт нумерованного списка — отдельная законченная мысль с новой строки: 1. 2. 3. (не дроби даты и номера статей вроде 3.4 или 18.07.2011).
Ключевые термины, названия разделов договора, статьи закона и заголовки этапов выделяй **жирным**.
Не используй markdown-разделители (`---`, `###`, `##`) — только **жирный** текст, списки и эмодзи 📄/📋/🔴/🟡/🟢.
Не используй заголовки «Общий вывод» и отдельную строку «Замечания:».

Формат отчёта о проверке — строго такой:

📄 Договор: [тип договора, если можно определить]
Предмет: [предмет договора]

📋 Вердикт: 🟢 МОЖНО ПОДПИСЫВАТЬ
или
📋 Вердикт: 🔴 ТРЕБУЕТ ДОРАБОТКИ / СОГЛАСОВАНИЯ РИСКОВ
С новой строки — 1–3 предложения: почему такой вердикт.

🔴 Критические замечания (риски недействительности, отсутствие существенных условий, прямые нарушения закона, явные налоговые риски)
1. Краткий заголовок замечания
Где: пункт / раздел договора
Суть: что в пункте написано
Обоснование: в чём ошибка или риск, со ссылкой на норму закона и [N] при опоре на текст договора

🟡 Важные замечания (риски при исполнении, споры с контрагентом, двусмысленности, пробелы в ответственности)
1. Краткий заголовок замечания
Где: пункт / раздел договора
Суть: что в пункте написано
Обоснование: в чём ошибка или риск, со ссылкой на норму закона и [N] при опоре на текст договора

Если в категории замечаний нет — одной строкой: «Не выявлено».
Каждое поле Где / Суть / Обоснование — с новой строки.
Для точечных вопросов по одному разделу отвечай по существу без обязательной структуры отчёта; ключевые термины всё равно выделяй **жирным**."""

# Начало пункта договора: «1.», «1.1», «3.2.», «10)» и т.п. в начале строки.
_CLAUSE_START_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s+\S")
# Ссылочный стиль: «п. 4.1», «пункт 12» в начале строки.
_P_REF_RE = re.compile(
    r"^\s*п(?:ункт(?:а|ом|е)?|[.])\s*(\d{1,3}(?:\.\d{1,2}){0,2})[.)]?\s+\S",
    re.IGNORECASE,
)


def _clause_number(line: str) -> str:
    m = _CLAUSE_START_RE.match(line) or _P_REF_RE.match(line)
    if not m:
        return ""
    return next((g for g in m.groups() if g), "")


def _fallback_fragments(text: str, chunk_chars: int = MAX_CONTRACT_FRAGMENT_CHARS) -> list[dict[str, Any]]:
    """Договор без нумерованных пунктов: нарезка по абзацам, не резать предложения."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    fragments: list[dict[str, Any]] = []
    current: list[str] = []
    current_len = 0
    for p in paragraphs:
        if current and current_len + len(p) + 2 > chunk_chars:
            fragments.append({"label": "", "text": "\n\n".join(current)})
            current = []
            current_len = 0
        current.append(p)
        current_len += len(p) + 2
    if current:
        fragments.append({"label": "", "text": "\n\n".join(current)})
    if not fragments:
        for i in range(0, len(text), chunk_chars):
            fragments.append({"label": "", "text": text[i : i + chunk_chars].strip()})
    return [f for f in fragments if f["text"].strip()]


def split_contract_fragments(full_text: str) -> list[dict[str, Any]]:
    """Разбивает текст договора на фрагменты-пункты с метками их номеров.

    Каждый фрагмент — {label: номер пункта или "", text: текст}. Для LLM фрагменты
    нумеруются сквозным [N] при сборке контекста.
    """
    text = (full_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    fragments: list[dict[str, Any]] = []
    current_lines: list[str] = []
    current_label = ""

    def _flush() -> None:
        nonlocal current_lines, current_label
        body = "\n".join(current_lines).strip()
        if body:
            fragments.append({"label": current_label or "", "text": body})
        current_lines = []
        current_label = ""

    chars_in_current = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        num = _clause_number(line)
        if num:
            _flush()
            current_label = num
            chars_in_current = 0
        current_lines.append(stripped)
        chars_in_current += len(stripped)
        if chars_in_current >= MAX_CONTRACT_FRAGMENT_CHARS:
            _flush()
            chars_in_current = 0
    _flush()

    if not any(f["label"] for f in fragments):
        return _fallback_fragments(text)
    return fragments


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def build_contract_context(
    fragments: list[dict[str, Any]],
    filename: str = "Договор",
    *,
    max_context_chars: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Собирает контекст (блок «Договор») и список источников [N] для LLM."""
    limit = max_context_chars or CHECK_LLM_CONTEXT_CHARS
    context_parts: list[str] = []
    citations: list[dict[str, Any]] = []
    context_len = 0

    for i, frag in enumerate(fragments, 1):
        label = str(frag.get("label") or "").strip()
        raw = strip_urls(str(frag.get("text") or "")).strip()
        if not raw:
            continue
        page = f"п. {label}" if label else f"фрагм. {i}"
        part = f"[{i}] {filename}, {page}:\n{raw}"
        if context_len + len(part) > limit:
            remaining = limit - context_len
            if remaining > 200:
                part = _truncate(part, remaining)
                context_parts.append(part)
                context_len += len(part)
                citations.append({"id": i, "filename": filename, "page": page})
            break
        context_parts.append(part)
        context_len += len(part)
        citations.append({"id": i, "filename": filename, "page": page})

    return "\n\n".join(context_parts), citations


def contract_summary(contract: dict[str, Any]) -> dict[str, Any]:
    """Краткий ответ API по загруженному договору (без текста фрагментов)."""
    return {
        "loaded": True,
        "filename": contract.get("filename") or "Договор",
        "total_chars": contract.get("total_chars", 0),
        "fragments": len(contract.get("fragments") or []),
        "pages": contract.get("pages", 0),
    }