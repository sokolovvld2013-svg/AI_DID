"""API-роутер модуля Секретарь."""
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from core.llm_errors import LLMUserFacingError
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import ALLOWED_AUDIO_EXT, BASE_DIR, MAX_AUDIO_SIZE, SECRETARY_UPLOAD_DIR
from core.history import secretary_history
from secretary.summarizer import build_protocol
from secretary.transcriber import transcribe

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/secretary", tags=["secretary"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _validate_audio(file: UploadFile) -> None:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_AUDIO_EXT:
        raise HTTPException(400, f"Допустимы форматы: {ALLOWED_AUDIO_EXT}")


@router.get("", response_class=HTMLResponse)
async def secretary_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="secretary.html",
        context={"active": "secretary", "history": secretary_history.list()},
    )


@router.post("/upload")
async def upload_audio(file: UploadFile = File(...)):
    _validate_audio(file)
    content = await file.read()
    if len(content) > MAX_AUDIO_SIZE:
        raise HTTPException(400, f"Файл превышает {MAX_AUDIO_SIZE // (1024*1024)} МБ")

    SECRETARY_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_id = str(uuid.uuid4())[:8]
    dest = SECRETARY_UPLOAD_DIR / f"{file_id}_{file.filename}"
    dest.write_bytes(content)

    try:
        transcript = transcribe(dest)
        if not transcript.strip():
            raise HTTPException(400, "Не удалось распознать речь в аудиофайле")
        protocol = build_protocol(transcript, file.filename or "")
    except HTTPException:
        raise
    except LLMUserFacingError as e:
        logger.warning("Ошибка LLM при обработке аудио: %s", e.original or e)
        raise HTTPException(500, e.user_message) from e
    except RuntimeError as e:
        logger.warning("Ошибка транскрибации: %s", e)
        raise HTTPException(500, str(e)) from e
    except Exception as e:
        logger.exception("Ошибка обработки аудио")
        raise HTTPException(500, "Ошибка обработки аудио. Попробуйте другой файл.") from e

    secretary_history.add(
        query=file.filename or "audio",
        response=protocol,
        file_id=file_id,
        filename=file.filename,
        transcript=transcript[:500] + ("..." if len(transcript) > 500 else ""),
    )

    return {
        "status": "ok",
        "filename": file.filename,
        "file_id": file_id,
        "transcript_preview": transcript[:300],
        "protocol": protocol,
    }


@router.get("/history")
async def history():
    return {"history": secretary_history.list()}


@router.get("/protocol/{file_id}")
async def get_protocol(file_id: str):
    for entry in secretary_history.list():
        if entry.get("file_id") == file_id:
            return {"protocol": entry["response"], "filename": entry.get("filename")}
    raise HTTPException(404, "Протокол не найден")
