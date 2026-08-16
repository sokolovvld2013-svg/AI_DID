"""Загрузка файлов в модуле Закупка (по образцу lawyer/router.py)."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from config import ALLOWED_DOC_EXT, MAX_DOCUMENT_SIZE
from lawyer.text_encoding import decode_upload_filename, repair_filename

logger = logging.getLogger(__name__)


def safe_stored_name(original: str, ext: str) -> str:
    import re

    stem = re.sub(r"[^\w.\-]", "_", Path(original).stem)[:80] or "document"
    return f"{stem}{ext}"


async def read_upload_file(file: UploadFile) -> tuple[bytes, str, str]:
    """Проверяет и читает загруженный файл. Возвращает (content, orig_name, ext)."""
    raw_name = decode_upload_filename(file.filename) or "document"
    ext = Path(raw_name).suffix.lower()
    if not ext:
        ext = ".txt"
        raw_name = f"{raw_name}{ext}"
    if ext not in ALLOWED_DOC_EXT:
        raise HTTPException(400, f"Допустимы форматы: {', '.join(sorted(ALLOWED_DOC_EXT))}")

    content = await file.read()
    if len(content) > MAX_DOCUMENT_SIZE:
        raise HTTPException(400, f"Файл превышает {MAX_DOCUMENT_SIZE // (1024 * 1024)} МБ")
    if not content:
        raise HTTPException(400, "Пустой файл")
    if ext == ".pdf" and not content.startswith(b"%PDF"):
        raise HTTPException(400, "Файл не является корректным PDF")

    orig_name = repair_filename(raw_name)
    if not Path(orig_name).suffix:
        orig_name = f"{orig_name}{ext}"
    return content, orig_name, ext


def write_temp_file(upload_dir: Path, content: bytes, orig_name: str, ext: str) -> Path:
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = safe_stored_name(orig_name, ext)
    temp_path = upload_dir / f"temp_{uuid.uuid4().hex[:10]}_{stored_name}"
    temp_path.write_bytes(content)
    logger.info(
        "Загружен %s: %s (%d байт) → %s",
        ext,
        orig_name,
        len(content),
        temp_path.name,
    )
    return temp_path
