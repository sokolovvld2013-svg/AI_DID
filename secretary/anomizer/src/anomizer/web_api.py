"""FastAPI-интеграция пайплайна анонимизации.

Готовый APIRouter для встраивания в ваше FastAPI-приложение:

    from fastapi import FastAPI
    from secretary.anomizer.src.anomizer.web_api import router as anonymize_router

    app = FastAPI()
    app.include_router(anonymize_router)

Эндпоинт:
    POST /api/anonymize   (multipart/form-data, поле "file" = *.docx|*.doc)

    - по умолчанию возвращает обезличенный .docx (attachment),
      отчёт — в заголовке X-Anonymizer-Report (percent-encoded JSON);
    - с параметром ?report=json возвращает JSON-отчёт вместо файла.

Эндпоинт подключается в корневом main.py:
    app.include_router(secretary.anomizer.src.anomizer.web_api.router)
"""

from __future__ import annotations

import json
import tempfile
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import JSONResponse, Response as FastAPIResponse

from .availability import ensure_available, unavailable_message, missing_dependencies
from .converter import ensure_docx, has_libreoffice
from .ner import parse_orgs_string
from .pipeline import anonymize_docx, MaskingReport

router = APIRouter(prefix="/api/anonymize", tags=["anonymize"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 МБ
SUPPORTED_SUFFIXES = {".doc", ".docx"}

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def anonymize_docx_bytes(data: bytes, filename: str, orgs: Optional[str] = None) -> Tuple[bytes, MaskingReport]:
    """Обезличить содержимое .doc/.docx-файла, вернуть (docx_bytes, отчёт).

    Чистая функция без FastAPI-типов — можно вызывать и из другого кода.
    Для .doc требует LibreOffice (иначе ValueError).
    orgs — строка организаций-контрагентов (вторая сторона) через запятую/перенос строки.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Неподдерживаемый формат: {suffix}. Допустимы: .doc, .docx"
        )
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Файл больше {MAX_UPLOAD_BYTES // 1024 // 1024} МБ")

    extra_orgs = parse_orgs_string(orgs)

    with tempfile.TemporaryDirectory(prefix="anomizer_api_") as tmpdir:
        tmp = Path(tmpdir)
        input_path = tmp / filename
        input_path.write_bytes(data)

        docx_path = ensure_docx(input_path, tmp)
        if docx_path is None:
            if suffix == ".doc" and not has_libreoffice():
                raise RuntimeError(
                    "LibreOffice не установлен: конвертация .doc недоступна"
                )
            raise RuntimeError("Не удалось подготовить .docx файл")

        output_path = tmp / f"{input_path.stem}_изм.docx"
        report = anonymize_docx(str(docx_path), str(output_path), extra_orgs=extra_orgs)
        return output_path.read_bytes(), report


def _report_headers(report: MaskingReport) -> dict:
    return {
        "X-Anonymizer-Entities": str(report.entities_found),
        "X-Anonymizer-By-Type": json.dumps(
            report.entities_by_type, ensure_ascii=True
        ),
        "X-Anonymizer-Report": urllib.parse.quote(
            report.to_json(), safe=""
        ),
    }


@router.get("/status")
async def anonymize_status():
    """Доступен ли модуль обезличивания (для интерфейса)."""
    missing = missing_dependencies()
    return {
        "available": not missing,
        "missing": missing,
        "detail": None if not missing else unavailable_message(missing),
    }


@router.post("", response_class=FastAPIResponse)
async def anonymize_endpoint(
    file: UploadFile = File(..., description="Word-документ (.doc или .docx)"),
    report: Optional[str] = Query(
        default=None, description="'json' — вернуть отчёт вместо файла"
    ),
    orgs: Optional[str] = Form(
        default=None,
        description="Организации-контрагенты (вторая сторона) через запятую — обезличиваются дополнительно к наименованию компании из настроек",
    ),
):
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Файл не передан")

    ensure_available()

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")

    try:
        docx_bytes, report_obj = anonymize_docx_bytes(data, file.filename, orgs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        msg = str(exc)
        if "LibreOffice" in msg:
            raise HTTPException(status_code=422, detail=msg)
        raise HTTPException(status_code=500, detail=msg)
    except ImportError as exc:
        # pymorphy2/natasha/pkg_resources — импорт ленивый, часть ошибок
        # всплывает только здесь; отдаём 503, а не 500.
        raise HTTPException(
            status_code=503,
            detail=unavailable_message([f"{exc.__class__.__name__}: {exc}"]),
        )
    except Exception as exc:  # внутренние ошибки пайплайна
        raise HTTPException(status_code=500, detail=f"Ошибка обработки: {exc}")

    if report == "json":
        return JSONResponse(content=json.loads(report_obj.to_json()))

    attachments = urllib.parse.quote(
        f"{Path(file.filename).stem}_изм.docx", safe=""
    )
    return Response(
        content=docx_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            ** _report_headers(report_obj),
            "Content-Disposition": (
                "attachment; filename*=UTF-8''" + attachments
            ),
        },
    )