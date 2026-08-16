"""Контекст LLM для проверки торговой документации."""

from __future__ import annotations

import json
from typing import Any

from config import MAX_LAWYER_CITATION_CHARS, MAX_LAWYER_LLM_CONTEXT_CHARS
from lawyer.text_encoding import strip_urls
from tenders.services.parser import DOC_LABELS
from tenders.services.validators import run_all_validations

CHECK_SYSTEM_PROMPT = """Ты — эксперт по аукционам аренды государственного/муниципального имущества (135-ФЗ, Приказ ФАС №147).

Правила:
- Анализируй фрагменты [1]–[4] и результаты автоматической проверки.
- Цитируй e-mail, адреса, кадастровые номера **дословно** как в документе.
- Не заменяй латиницу в e-mail и доменах на кириллические «двойники».
- Учитывай выводы автоматической проверки, но формулируй итог для пользователя.

Формат отчёта:

**Общий вывод:** (кратко; укажи оценку 0–100 и статус: passed / warnings / failed)

**Замечания:**

🔴 **Критические** (нарушения 135-ФЗ, Приказ ФАС №147, расхождения между документами, риск отмены):
- пункты списка со ссылками [N]

🟡 **Важные** (риски при проведении торгов, споры с участниками):
- пункты списка со ссылками [N]

Если замечаний нет — «Не выявлено»."""

EXPERT_SYSTEM_PROMPT = """Ты — эксперт по аукционам аренды государственного и муниципального имущества (135-ФЗ, Приказ ФАС №147).

Правила:
- Опирайся на 135-ФЗ, Приказ ФАС №147 и смежные акты из своих знаний.
- Отвечай структурированно, на русском языке, по существу вопроса.
- Указывай конкретные нормы закона (статьи, пункты), где это уместно.
- Не выдумывай реквизиты документов и данные объектов, которых нет в вопросе.
- Если вопрос выходит за рамки компетенции — скажи об этом прямо."""


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _format_checks(validation: dict[str, Any]) -> str:
    lines = [
        f"Статус автопроверки: {validation.get('status')} (оценка {validation.get('score')}/100)",
    ]
    for e in (validation.get("errors") or [])[:20]:
        lines.append(f"  ОШИБКА [{e.get('document')}/{e.get('field')}]: {e.get('message')}")
    for w in (validation.get("warnings") or [])[:15]:
        lines.append(f"  ПРЕДУПР. [{w.get('document')}/{w.get('field')}]: {w.get('message')}")
    return "\n".join(lines)


def build_check_context(
    auction: dict[str, Any],
    egrn: dict[str, Any],
    approval: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    validation = run_all_validations(auction, egrn, approval)

    docs = [
        (1, "auction", auction),
        (2, "egrn", egrn),
        (3, "approval", approval),
    ]
    context_parts: list[str] = []
    citations: list[dict[str, Any]] = []
    context_len = 0

    for num, key, doc in docs:
        label = DOC_LABELS.get(key, key)
        filename = doc.get("filename") or label
        fields_json = json.dumps(doc.get("fields") or {}, ensure_ascii=False, indent=0)
        body = _truncate(strip_urls(doc.get("text") or ""), MAX_LAWYER_CITATION_CHARS // 2)
        part = (
            f"[{num}] {filename} ({label}):\n"
            f"Извлечённые поля: {fields_json}\n"
            f"Текст:\n{body}"
        )
        if context_len + len(part) > MAX_LAWYER_LLM_CONTEXT_CHARS - 2000:
            remaining = MAX_LAWYER_LLM_CONTEXT_CHARS - context_len - 2000
            if remaining > 300:
                part = _truncate(part, remaining)
                context_parts.append(part)
                context_len += len(part)
            break
        context_parts.append(part)
        context_len += len(part)
        citations.append({
            "id": num,
            "filename": filename,
            "page": label,
            "section": label,
        })

    cross_part = (
        f"[4] Перекрёстная проверка и автоматические правила:\n{_format_checks(validation)}"
    )
    if context_len + len(cross_part) <= MAX_LAWYER_LLM_CONTEXT_CHARS:
        context_parts.append(cross_part)
        citations.append({
            "id": 4,
            "filename": "Перекрёстная проверка",
            "page": "сводка",
            "section": "cross",
        })

    return "\n\n".join(context_parts), citations, validation
