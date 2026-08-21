"""API-роутер модуля «Торги»."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from config import (
    ALLOWED_AUCTION_EXT,
    ALLOWED_TENDERS_PDF_EXT,
    BASE_DIR,
    TENDERS_ACCESS_TOKEN,
    TENDERS_UPLOAD_DIR,
)
from core.history import tenders_history
from core.llm_client import get_llm
from core.llm_errors import LLMUserFacingError
from core.session import get_session_id
from lawyer.citations import select_citations_for_display
from lawyer.text_encoding import clean_llm_display_text, repair_filename, strip_urls
from core.prompt_guards import EXPERT_REFUSAL_HINT
from tenders.services.cache_store import get_by_doc_id, get_by_hash, save_parsed
from tenders.services.check_context import (
    CHECK_SYSTEM_PROMPT,
    EXPERT_SYSTEM_PROMPT,
    build_check_context,
)
from tenders.services.file_upload import read_upload_file, write_temp_file
from tenders.services.parser import PARSE_VERSION, parse_document, summary_for_client
from tenders.session_state import (
    ZONES,
    all_loaded,
    clear_documents,
    get_document,
    get_documents,
    set_document,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenders", tags=["tenders"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

CHECK_QUESTION = (
    "Проверь комплект торговой документации (аукцион, выписка ЕГРН, согласование) "
    "и сформируй отчёт о проверке с замечаниями."
)

ZONE_LABELS = {
    "auction": "Торговая документация",
    "egrn": "Выписка из ЕГРН",
    "approval": "Согласование сделки",
}

ZONE_EXT = {
    "auction": ALLOWED_AUCTION_EXT,
    "egrn": ALLOWED_TENDERS_PDF_EXT,
    "approval": ALLOWED_TENDERS_PDF_EXT,
}


class TendersQuery(BaseModel):
    question: str = CHECK_QUESTION
    mode: Literal["check", "expert"] = "check"


class TendersExpertQuery(BaseModel):
    question: str


def _check_access(request: Request) -> None:
    if not TENDERS_ACCESS_TOKEN:
        return
    token = request.headers.get("X-Tenders-Token") or request.query_params.get("token")
    if token != TENDERS_ACCESS_TOKEN:
        raise HTTPException(403, "Нет доступа к модулю «Торги»")


def _citation_ref(citation: dict) -> dict:
    return {
        "id": citation.get("id"),
        "filename": strip_urls(repair_filename(citation.get("filename") or "")),
        "page": citation.get("page"),
        "file_id": citation.get("file_id"),
    }


def _error_reply(
    session_id: str,
    question: str,
    message: str,
    mode: str = "check",
) -> dict:
    tenders_history.add(session_id, question, message, mode=mode)
    return {"answer": message, "citations": [], "verification": None}


@router.get("", response_class=HTMLResponse)
async def tenders_page(request: Request):
    _check_access(request)
    sid = get_session_id(request)
    docs = get_documents(sid)
    return templates.TemplateResponse(
        request=request,
        name="tenders.html",
        context={
            "active": "tenders",
            "history": tenders_history.list(sid),
            "documents": docs,
        },
    )


@router.get("/status")
async def documents_status(request: Request):
    _check_access(request)
    sid = get_session_id(request)
    docs = get_documents(sid)
    result: dict = {"all_loaded": all_loaded(sid), "zones": {}}
    for zone in ZONES:
        meta = docs.get(zone)
        if not meta:
            result["zones"][zone] = {"loaded": False}
            continue
        parsed = get_by_doc_id(meta["doc_id"])
        if not parsed:
            result["zones"][zone] = {"loaded": False}
            continue
        summary = summary_for_client(parsed)
        summary["loaded"] = True
        summary["filename"] = meta.get("filename") or summary.get("filename")
        result["zones"][zone] = summary
    if not all(result["zones"].get(z, {}).get("loaded") for z in ZONES):
        result["all_loaded"] = False
    return result


async def _upload_zone(request: Request, zone: str, file: UploadFile) -> dict:
    _check_access(request)
    sid = get_session_id(request)
    if zone not in ZONES:
        raise HTTPException(400, "Неизвестный тип документа")

    content, orig_name, ext = await read_upload_file(file, allowed_ext=ZONE_EXT[zone])
    temp_path = write_temp_file(TENDERS_UPLOAD_DIR, content, orig_name, ext)
    try:
        parsed = parse_document(temp_path, doc_type=zone, filename=orig_name)
        cached = get_by_hash(parsed["file_hash"])
        if cached and cached.get("parse_version") == PARSE_VERSION:
            doc_id = cached["doc_id"]
            result = summary_for_client(cached)
            result["cached"] = True
        else:
            save_parsed(parsed)
            doc_id = parsed["doc_id"]
            result = summary_for_client(parsed)
            result["cached"] = False
        set_document(sid, zone, doc_id, orig_name)
        result["status"] = "ok"
        result["zone"] = zone
        result["filename"] = orig_name
        return result
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        temp_path.unlink(missing_ok=True)


@router.post("/auction/upload")
async def upload_auction(request: Request, file: UploadFile = File(...)):
    return await _upload_zone(request, "auction", file)


@router.post("/egrn/upload")
async def upload_egrn(request: Request, file: UploadFile = File(...)):
    return await _upload_zone(request, "egrn", file)


@router.post("/approval/upload")
async def upload_approval(request: Request, file: UploadFile = File(...)):
    return await _upload_zone(request, "approval", file)


@router.post("/query")
async def query_check(request: Request, body: TendersQuery):
    """Проверка комплекта торговой документации (требуются все 3 файла)."""
    _check_access(request)
    sid = get_session_id(request)
    question = (body.question or CHECK_QUESTION).strip() or CHECK_QUESTION
    return await _query_check(sid, question)


@router.post("/expert/query")
async def query_expert(request: Request, body: TendersExpertQuery):
    """Экспертное мнение — без загрузки документов."""
    _check_access(request)
    sid = get_session_id(request)
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(400, "Пустой вопрос")
    return await _query_expert(sid, question)


async def _query_check(session_id: str, question: str) -> dict:
    sid = session_id

    if not all_loaded(sid):
        raise HTTPException(
            400,
            "Загрузите все три документа: торговую документацию (DOCX), "
            "выписку из ЕГРН (PDF) и согласование сделки (PDF).",
        )

    docs = get_documents(sid)
    parsed_docs: dict[str, dict] = {}
    for zone in ZONES:
        meta = docs.get(zone)
        if not meta:
            clear_documents(sid)
            raise HTTPException(400, "Документы устарели. Загрузите файлы заново.")
        parsed = get_by_doc_id(meta["doc_id"])
        if not parsed:
            clear_documents(sid)
            raise HTTPException(400, "Документы устарели. Загрузите файлы заново.")
        parsed_docs[zone] = parsed

    context, citations, validation = build_check_context(
        parsed_docs["auction"],
        parsed_docs["egrn"],
        parsed_docs["approval"],
    )

    try:
        llm = get_llm()
        raw_answer = llm.generate(
            f"Запрос: {question}\n\n"
            "Проверь комплект документов по фрагментам ниже. "
            "Сформируй отчёт в формате: 📄 Документация, Предмет, НМЦД (если есть), 📋 Вердикт, "
            "затем 🔴 Критические замечания и 🟡 Важные замечания "
            "с полями Где / Суть / Обоснование у каждого пункта. "
            "Укажи номера [N] использованных фрагментов.",
            system_prompt=CHECK_SYSTEM_PROMPT,
            context=context,
        )
        answer = clean_llm_display_text(raw_answer)
        citations = select_citations_for_display(answer, citations)
        tenders_history.add(
            sid,
            question,
            answer,
            mode="check",
            citations=citations,
            verification=validation,
        )
        return {
            "answer": answer,
            "citations": [_citation_ref(c) for c in citations],
            "verification": {
                "status": validation.get("status"),
                "score": validation.get("score"),
                "errors_count": len(validation.get("errors") or []),
                "warnings_count": len(validation.get("warnings") or []),
            },
        }
    except LLMUserFacingError as e:
        logger.warning("Tenders check LLM error: %s", e.original or e)
        return _error_reply(sid, question, e.user_message, "check")
    except Exception as e:
        logger.exception("Tenders check failed: %s", e)
        return _error_reply(
            sid,
            question,
            "Произошла ошибка при проверке. Попробуйте позже.",
            "check",
        )


async def _query_expert(session_id: str, question: str) -> dict:
    try:
        llm = get_llm()
        raw_answer = llm.generate(
            f"Вопрос пользователя: {question}\n\n"
            "Отвечай только по действующему праву (135-ФЗ, Приказ ФАС № 147/23 и иные актуальные акты). "
            "Не ссылайся на Приказ ФАС № 67 и другие утратившие силу акты. "
            f"{EXPERT_REFUSAL_HINT}",
            system_prompt=EXPERT_SYSTEM_PROMPT,
        )
        answer = clean_llm_display_text(raw_answer)
        tenders_history.add(session_id, question, answer, mode="expert")
        return {"answer": answer, "citations": [], "verification": None}
    except LLMUserFacingError as e:
        logger.warning("Tenders expert LLM error: %s", e.original or e)
        return _error_reply(session_id, question, e.user_message, "expert")
    except Exception as e:
        logger.exception("Tenders expert failed: %s", e)
        return _error_reply(
            session_id,
            question,
            "Произошла ошибка при консультации. Попробуйте переформулировать вопрос.",
            "expert",
        )


@router.get("/history")
async def history(request: Request):
    _check_access(request)
    sid = get_session_id(request)
    repaired = []
    for item in tenders_history.list(sid):
        entry = dict(item)
        entry["query"] = entry.get("query") or ""
        entry["response"] = clean_llm_display_text(entry.get("response") or "")
        if entry.get("citations"):
            entry["citations"] = [_citation_ref(c) for c in entry["citations"]]
        repaired.append(entry)
    return {"history": repaired}
