"""Проверка repair_text для латинских двойников кириллицы."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawyer.text_encoding import repair_text

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
