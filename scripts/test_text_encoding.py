"""Проверка repair_text для cp1251-mojibake."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawyer.text_encoding import (
    _word_has_mixed_homoglyphs,
    citation_needs_llm_repair,
    repair_citation_text,
    repair_text,
)

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+", re.UNICODE)

SAMPLE = (
    "РќР° РѕСЃРЅРѕРІРµ РїСЂРµРґРѕСЃС‚Р°РІР»РµРЅРЅС‹С… "
    "С„СЂР°РіРјРµРЅС‚РѕРІ РёРЅС„РѕСЂРјР°С†РёРё"
)

fixed = repair_text(SAMPLE)
assert "РќР°" not in fixed, fixed
assert "на основе" in fixed.lower(), fixed
print("OK mojibake:", fixed[:80], "...")

OCR_SAMPLE = (
    "11 СоМрОВОКИеННе меТеВКону B НерВыИ JeHb yue6Horo rOna "
    "(1epВВИ1 pa3B НерВыИУЖЛИасс) МНОрОЛеТНВИМ рОИНТеЛАМ"
)
ocr_fixed = repair_citation_text(OCR_SAMPLE)
mixed = [m.group(0) for m in _WORD_RE.finditer(ocr_fixed) if _word_has_mixed_homoglyphs(m.group(0))]
assert not mixed, (mixed, ocr_fixed)
assert citation_needs_llm_repair(OCR_SAMPLE), "LLM repair should be triggered for OCR soup"
assert ocr_fixed == ocr_fixed.lower() or "сомровокиенне" in ocr_fixed.lower(), ocr_fixed
print("OK ocr:", ocr_fixed[:100], "...")
