"""Проверка repair_text для латинских двойников кириллицы."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawyer.text_encoding import decode_text_bytes, repair_text

SAMPLES = [
    "EIEHNI,OTHOCHMbIX CJyKeOHOn",
    "OTHOCHMbIX CJyKeOHOn",
    "CJyKeOHOn",
    "РџСЂРёРєР°Р· № 123",
    "Нормальный русский текст",
    "The quick brown fox",
]

for s in SAMPLES:
    print("IN :", s)
    print("OUT:", repair_text(s))
    print()

# TXT: CP1251, ошибочно прочитанный как UTF-8 (пїЅ…)
sample_ru = "Положение о закупке товаров для нужд организации"
raw_cp1251 = sample_ru.encode("cp1251")
broken = raw_cp1251.decode("utf-8", errors="replace")
print("Broken UTF-8 read:", broken[:40], "...")
fixed = decode_text_bytes(raw_cp1251)
print("decode_text_bytes:", fixed)
assert "Положение" in fixed and "пїЅ" not in fixed
print("CP1251 round-trip: OK")
