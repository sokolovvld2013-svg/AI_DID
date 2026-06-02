"""Восстановление читаемого текста цитат из искажённого PDF/OCR."""

import logging

from config import LAWYER_CITATION_LLM_REPAIR, MAX_LAWYER_CITATION_CHARS
from core.llm_client import BaseLLM
from lawyer.text_encoding import citation_needs_llm_repair, repair_citation_text

logger = logging.getLogger(__name__)

_CITATION_SYSTEM = (
    "Ты восстанавливаешь русский текст из фрагмента PDF или OCR. "
    "Исправь смешанный регистр букв, латинские буквы-двойники кириллицы, "
    "типичные ошибки распознавания. Сохрани числа, скобки, смысл и стиль нормативного документа. "
    "Ответ — только исправленный фрагмент, без пояснений и кавычек."
)


def repair_citation_display(text: str, llm: BaseLLM | None = None) -> str:
    """Текст для блока «Источники»: эвристики + при необходимости LLM."""
    raw = text or ""
    cleaned = repair_citation_text(raw)
    needs_llm = citation_needs_llm_repair(raw) or citation_needs_llm_repair(cleaned)
    if not cleaned or not needs_llm:
        return cleaned
    if not LAWYER_CITATION_LLM_REPAIR or llm is None:
        return cleaned

    limit = min(MAX_LAWYER_CITATION_CHARS, 2400)
    snippet = cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"
    try:
        fixed = llm.generate(
            f"Исправь фрагмент:\n\n{snippet}",
            system_prompt=_CITATION_SYSTEM,
        )
        fixed = (fixed or "").strip()
        if fixed and len(fixed) >= len(cleaned) * 0.35:
            return fixed
    except Exception as e:
        logger.warning("LLM repair citation failed: %s", e)
    return cleaned
