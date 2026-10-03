"""API-роутер модуля Секретарь."""
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from config import ALLOWED_AUDIO_EXT, MAX_AUDIO_SIZE, SECRETARY_UPLOAD_DIR
from core.history import secretary_history
from core.llm_client import current_usage
from core.llm_errors import LLMUserFacingError, llm_error_code
from core.session import get_session_id
from core.templates import templates
from core.user_logs import log_query
from secretary.summarizer import build_protocol
from secretary.transcriber import transcribe

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/secretary", tags=["secretary"])


def _validate_audio(file: UploadFile) -> None:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_AUDIO_EXT:
        raise HTTPException(400, f"Допустимы форматы: {ALLOWED_AUDIO_EXT}")


@router.get("", response_class=HTMLResponse)
async def secretary_page(request: Request):
    sid = get_session_id(request)
    return templates.TemplateResponse(
        request=request,
        name="secretary.html",
        context={"active": "secretary", "history": secretary_history.list(sid)},
    )


@router.post("/upload")
async def upload_audio(request: Request, file: UploadFile = File(...)):
    # Отклонённые файлы тоже попадают в лог: иначе в статистике «все ответы
    # успешны», хотя пользователи загружают не то.
    try:
        _validate_audio(file)
        content = await file.read()
        if len(content) > MAX_AUDIO_SIZE:
            raise HTTPException(400, f"Файл превышает {MAX_AUDIO_SIZE // (1024*1024)} МБ")
    except HTTPException:
        log_query(
            module="secretary",
            question=f"[аудио] {file.filename or 'без имени'}",
            status="error",
            tokens=current_usage(),
            error_code="AUDIO_INVALID",
        )
        raise

    SECRETARY_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_id = str(uuid.uuid4())[:8]
    dest = SECRETARY_UPLOAD_DIR / f"{file_id}_{file.filename}"
    dest.write_bytes(content)
    # Вопросом считаем загруженный аудиофайл: отдельного текстового поля нет.
    _log_question = f"[аудио] {file.filename or file_id}"

    try:
        transcript = transcribe(dest)
        if not transcript.strip():
            raise HTTPException(400, "Не удалось распознать речь в аудиофайле")
        protocol = build_protocol(transcript, file.filename or "")
    except HTTPException:
        log_query(
            module="secretary",
            question=_log_question,
            status="error",
            tokens=current_usage(),
            error_code="AUDIO_UNREADABLE",
        )
        raise
    except LLMUserFacingError as e:
        logger.warning("Ошибка LLM при обработке аудио: %s", e.original or e)
        log_query(
            module="secretary",
            question=_log_question,
            status="error",
            tokens=current_usage(),
            error_code=llm_error_code(e.original or e),
        )
        raise HTTPException(500, e.user_message) from e
    except RuntimeError as e:
        logger.warning("Ошибка транскрибации: %s", e)
        log_query(
            module="secretary",
            question=_log_question,
            status="error",
            tokens=current_usage(),
            error_code="AUDIO_TRANSCRIBE_FAILED",
        )
        raise HTTPException(500, str(e)) from e
    except Exception as e:
        logger.exception("Ошибка обработки аудио")
        _msg = "Ошибка обработки аудио. Попробуйте другой файл."
        log_query(
            module="secretary",
            question=_log_question,
            status="error",
            tokens=current_usage(),
            error_code="PROTOCOL_BUILD_FAILED",
        )
        raise HTTPException(500, _msg) from e

    sid = get_session_id(request)
    secretary_history.add(
        sid,
        query=file.filename or "audio",
        response=protocol,
        file_id=file_id,
        filename=file.filename,
        transcript=transcript[:500] + ("..." if len(transcript) > 500 else ""),
    )

    log_query(
        module="secretary",
        question=_log_question,
        status="ok",
        tokens=current_usage(),
    )

    return {
        "status": "ok",
        "filename": file.filename,
        "file_id": file_id,
        "transcript_preview": transcript[:300],
        "protocol": protocol,
    }


@router.get("/history")
async def history(request: Request):
    return {"history": secretary_history.list(get_session_id(request))}


@router.get("/protocol/{file_id}")
async def get_protocol(file_id: str, request: Request):
    sid = get_session_id(request)
    for entry in secretary_history.list(sid):
        if entry.get("file_id") == file_id:
            return {"protocol": entry["response"], "filename": entry.get("filename")}
    raise HTTPException(404, "Протокол не найден")
