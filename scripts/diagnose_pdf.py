"""Проверка извлечения текста из PDF (только Python-пакеты)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lawyer.doc_processor import (  # noqa: E402
    _pdf_security_hint,
    _read_pdf_pdfium,
    _read_pdf_pdfminer,
    _read_pdf_pymupdf,
    _read_pdf_pdfplumber,
    _read_pdf_pypdf,
    _read_pdf_rapidocr,
    pymupdf_available,
)


def main() -> None:
    if len(sys.argv) < 2:
        print("Укажите путь к PDF")
        sys.exit(1)
    path = Path(sys.argv[1])
    if not path.is_file():
        print("Файл не найден:", path)
        sys.exit(1)

    print("Файл:", path)
    print("Размер:", path.stat().st_size, "байт")
    print("pymupdf:", "да" if pymupdf_available() else "НЕТ")
    sec = _pdf_security_hint(path)
    if sec:
        print("Безопасность:", sec)

    readers = [
        ("PyMuPDF", _read_pdf_pymupdf),
        ("pypdfium2", _read_pdf_pdfium),
        ("pdfplumber", _read_pdf_pdfplumber),
        ("pdfminer", _read_pdf_pdfminer),
        ("pypdf", _read_pdf_pypdf),
    ]
    for name, fn in readers:
        try:
            pages = fn(path)
            chars = sum(len(p["text"]) for p in pages)
            print(f"  {name:12} → {len(pages):3} стр., {chars:6} симв.")
        except Exception as e:
            print(f"  {name:12} → ОШИБКА: {e}")

    try:
        pages, err = _read_pdf_rapidocr(path)
        chars = sum(len(p["text"]) for p in pages)
        print(f"  {'RapidOCR':12} → {len(pages):3} стр., {chars:6} симв.", end="")
        if err:
            print(f" ({err})")
        else:
            print()
    except Exception as e:
        print(f"  {'RapidOCR':12} → ОШИБКА: {e}")


if __name__ == "__main__":
    main()
