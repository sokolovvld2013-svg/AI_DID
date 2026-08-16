"""API-роутер модуля «Закупка 223-ФЗ»."""
import logging
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from config import (
    BASE_DIR,
    MAX_LAWYER_CITATION_CHARS,
    MAX_LAWYER_LLM_CONTEXT_CHARS,
    PROCUREMENT_ACCESS_TOKEN,
    PROCUREMENT_POLICY_UPLOAD_DIR,
    PROCUREMENT_UPLOAD_DIR,
)
from core.history import procurement_history
from core.llm_client import get_llm
from core.llm_errors import LLMUserFacingError
from core.session import get_session_id
from lawyer.citations import select_citations_for_display
from lawyer.doc_processor import process_upload
from lawyer.router import _select_relevant_hits
from lawyer.text_encoding import (
    repair_filename,
    strip_urls,
)
from procurement.kb_rag import get_policy_rag
from procurement.services.cache_store import get_by_audit_id, get_by_hash, save_parsed
from procurement.services.check_context import CHECK_SYSTEM_PROMPT, build_check_context
from procurement.services.file_upload import read_upload_file, safe_stored_name, write_temp_file
from procurement.services.parser import PARSE_VERSION, parse_documentation, summary_for_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/procurement", tags=["procurement"])
legacy_router = APIRouter(tags=["procurement"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _policy_rag():
    return get_policy_rag()

EXPERT_SYSTEM_PROMPT = """Ты — эксперт по закупкам по 223-ФЗ и внутренним регламентам заказчика.

Правила:
- Опирайся на фрагменты Положения о закупке [1], [2], … (если они переданы ниже).
- Дополняй ответ общими нормами 223-ФЗ, 135-ФЗ и иных актов из своих знаний; нормы закона формулируй без номера [N], для Положения — только с [N].
- Не выдумывай пункты Положения — для него используй только переданные фрагменты с номерами [N].
- В ответе обязательно указывай номера использованных фрагментов Положения: [1], [2] (только те, на которые опираешься).
- Отвечай на русском языке, кратко и по существу."""


class ProcurementQuery(BaseModel):
    question: str
    mode: Literal["check", "expert"] = "check"


def _check_access(request: Request) -> None:
    if not PROCUREMENT_ACCESS_TOKEN:
        return
    token = request.headers.get("X-Procurement-Token") or request.query_params.get("token")
    if token != PROCUREMENT_ACCESS_TOKEN:
        raise HTTPException(403, "Нет доступа к модулю «Закупка»")


from procurement.session_state import clear_documentation, get_documentation, set_documentation


def _citation_ref(citation: dict) -> dict:
    return {
        "id": citation.get("id"),
        "filename": strip_urls(repair_filename(citation.get("filename") or "")),
        "page": citation.get("page"),
        "file_id": citation.get("file_id"),
    }


def _truncate_fragment(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _error_reply(session_id: str, question: str, message: str, mode: str) -> dict:
    procurement_history.add(session_id, question, message, mode=mode)
    return {"answer": message, "citations": []}


@router.get("", response_class=HTMLResponse)
async def procurement_page(request: Request):
    _check_access(request)
    sid = get_session_id(request)
    doc = get_documentation(sid)
    return templates.TemplateResponse(
        request=request,
        name="procurement.html",
        context={
            "active": "procurement",
            "history": procurement_history.list(sid),
            "documentation": doc,
            "policy_files": _policy_rag().list_files(),
        },
    )


@router.get("/documentation/status")
async def documentation_status(request: Request):
    _check_access(request)
    sid = get_session_id(request)
    doc = get_documentation(sid)
    if not doc:
        return {"loaded": False}
    parsed = get_by_audit_id(doc["audit_id"])
    if not parsed:
        clear_documentation(sid)
        return {"loaded": False}
    summary = summary_for_client(parsed)
    summary["loaded"] = True
    summary["filename"] = doc.get("filename") or summary.get("filename")
    return summary


@router.post("/documentation/upload")
@router.post("/upload/documentation")
async def upload_documentation(request: Request, file: UploadFile = File(...)):
    """Загрузка закупочной документации (DOCX, TXT, PDF) → разбор разделов."""
    _check_access(request)
    sid = get_session_id(request)

    content, orig_name, ext = await read_upload_file(file)
    temp_path = write_temp_file(PROCUREMENT_UPLOAD_DIR, content, orig_name, ext)

    try:
        parsed = parse_documentation(temp_path, filename=orig_name)
        cached = get_by_hash(parsed["file_hash"])
        if cached and cached.get("parse_version") == PARSE_VERSION:
            audit_id = cached["audit_id"]
            result = summary_for_client(cached)
            result["cached"] = True
        else:
            save_parsed(parsed)
            audit_id = parsed["audit_id"]
            result = summary_for_client(parsed)
            result["cached"] = False

        set_documentation(sid, audit_id, orig_name)
        result["status"] = "ok"
        result["file_id"] = audit_id
        result["filename"] = orig_name
        return result
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        logger.exception("Ошибка разбора документации: %s", orig_name)
        raise HTTPException(500, f"Ошибка обработки: {e}") from e
    finally:
        temp_path.unlink(missing_ok=True)


@router.delete("/documentation")
async def delete_documentation(request: Request):
    _check_access(request)
    clear_documentation(get_session_id(request))
    return {"status": "ok"}


@router.get("/policy/files")
async def list_policy_files():
    return {"files": _policy_rag().list_files()}


@router.post("/policy/upload")
@router.post("/upload")
async def upload_policy(file: UploadFile = File(...)):
    """Загрузка Положения о закупке — как /lawyer/upload."""
    content, orig_name, ext = await read_upload_file(file)
    # Временный файл — не в policy/: clear_all() очищает эту папку
    temp_path = write_temp_file(PROCUREMENT_UPLOAD_DIR, content, orig_name, ext)
    rag = _policy_rag()

    try:
        file_id, chunks = process_upload(temp_path, orig_name)
        if not chunks:
            raise ValueError("Не удалось проиндексировать Положение (пустые фрагменты)")

        rag.clear_all()
        PROCUREMENT_POLICY_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = PROCUREMENT_POLICY_UPLOAD_DIR / f"{file_id}_{safe_stored_name(orig_name, ext)}"
        dest.write_bytes(content)

        count = rag.add_chunks(chunks)
        if count == 0:
            raise ValueError("Не удалось проиндексировать Положение (пустые фрагменты)")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        logger.exception("Ошибка эмбеддингов при загрузке Положения")
        raise HTTPException(503, str(e)) from e
    except Exception as e:
        logger.exception("Ошибка обработки Положения")
        raise HTTPException(500, f"Ошибка: {e}") from e
    finally:
        temp_path.unlink(missing_ok=True)

    return {
        "status": "ok",
        "file_id": file_id,
        "filename": orig_name,
        "chunks": count,
    }


@router.delete("/policy/files/{file_id}")
async def delete_policy_file(file_id: str):
    if not _policy_rag().delete_file(file_id):
        raise HTTPException(404, "Файл не найден")
    return {"status": "ok", "file_id": file_id}


@router.post("/query")
async def query(req: ProcurementQuery, request: Request):
    _check_access(request)
    question = req.question.strip()
    sid = get_session_id(request)
    mode = req.mode

    if not question:
        raise HTTPException(400, "Пустой вопрос")

    if mode == "check":
        return await _query_check(sid, question)
    return await _query_expert(sid, question)


async def _query_check(session_id: str, question: str) -> dict:
    doc = get_documentation(session_id)
    if not doc:
        raise HTTPException(
            400,
            "Сначала загрузите закупочную документацию в блоке «Закупочная документация».",
        )

    parsed = get_by_audit_id(doc["audit_id"])
    if not parsed:
        clear_documentation(session_id)
        raise HTTPException(
            400,
            "Документация устарела. Загрузите файл заново.",
        )

    context, citations = build_check_context(parsed, question)
    if not context:
        msg = (
            "Не удалось извлечь разделы документации для проверки. "
            "Убедитесь, что файл содержит стандартные разделы (информационная карта, ТЗ, договор и т.д.)."
        )
        procurement_history.add(session_id, question, msg, mode="check")
        return {"answer": msg, "citations": []}

    try:
        llm = get_llm()
        raw_answer = llm.generate(
            f"Вопрос пользователя: {question}\n\n"
            "Проверь закупочную документацию по фрагментам ниже. "
            "Сформируй отчёт о проверке: Общий вывод, затем блоки 🔴 Критические и 🟡 Важные. "
            "Укажи номера [N] использованных фрагментов.",
            system_prompt=CHECK_SYSTEM_PROMPT,
            context=context,
        )
        answer = strip_urls(raw_answer)
        citations = select_citations_for_display(answer, citations)
        procurement_history.add(
            session_id,
            question,
            answer,
            mode="check",
            citations=citations,
        )
        return {
            "answer": answer,
            "citations": [_citation_ref(c) for c in citations],
        }
    except LLMUserFacingError as e:
        logger.warning("Procurement check LLM error: %s", e.original or e)
        return _error_reply(session_id, question, e.user_message, "check")
    except Exception as e:
        logger.exception("Procurement check failed: %s", e)
        return _error_reply(
            session_id,
            question,
            "Произошла ошибка при проверке. Попробуйте переформулировать запрос.",
            "check",
        )


async def _query_expert(session_id: str, question: str) -> dict:
    policy_hits = _policy_rag().search(question)
    citations: list[dict] = []
    context = ""

    if policy_hits:
        hits = _select_relevant_hits(question, policy_hits)
        if hits:
            context_parts: list[str] = []
            context_len = 0
            for i, hit in enumerate(hits, 1):
                merged = _policy_rag().merge_neighbor_context(hit)
                raw_text = _truncate_fragment(
                    strip_urls(merged or hit.get("text") or ""),
                    MAX_LAWYER_CITATION_CHARS,
                )
                filename = strip_urls(repair_filename(hit["filename"] or ""))
                part = f"[{i}] {filename}, стр. {hit['page']}:\n{raw_text}"
                if context_len + len(part) > MAX_LAWYER_LLM_CONTEXT_CHARS:
                    remaining = MAX_LAWYER_LLM_CONTEXT_CHARS - context_len
                    if remaining > 200:
                        part = _truncate_fragment(part, remaining)
                        context_parts.append(part)
                        context_len += len(part)
                    break
                context_parts.append(part)
                context_len += len(part)
                citations.append({
                    "id": i,
                    "filename": filename,
                    "page": hit["page"],
                    "file_id": hit["file_id"],
                })
            context = "\n\n".join(context_parts)

    user_prompt = f"Вопрос пользователя: {question}\n\n"
    if context:
        user_prompt += (
            "Используй фрагменты Положения о закупке ниже (номера [N]) "
            "и при необходимости общие нормы законодательства о закупках."
        )
    else:
        user_prompt += (
            "Положение о закупке не загружено или не найдено по запросу. "
            "Ответь на основе законодательства о закупках (223-ФЗ и смежные акты). "
            "Не ссылайся на пункты Положения."
        )

    try:
        llm = get_llm()
        raw_answer = llm.generate(
            user_prompt,
            system_prompt=EXPERT_SYSTEM_PROMPT,
            context=context or None,
        )
        answer = strip_urls(raw_answer)
        if citations:
            citations = select_citations_for_display(answer, citations)
        procurement_history.add(
            session_id,
            question,
            answer,
            mode="expert",
            citations=citations,
        )
        return {
            "answer": answer,
            "citations": [_citation_ref(c) for c in citations],
        }
    except LLMUserFacingError as e:
        logger.warning("Procurement expert LLM error: %s", e.original or e)
        return _error_reply(session_id, question, e.user_message, "expert")
    except Exception as e:
        logger.exception("Procurement expert failed: %s", e)
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
    for item in procurement_history.list(sid):
        entry = dict(item)
        entry["query"] = entry.get("query") or ""
        entry["response"] = strip_urls(entry.get("response") or "")
        if entry.get("citations"):
            entry["citations"] = [_citation_ref(c) for c in entry["citations"]]
        repaired.append(entry)
    return {"history": repaired}


@legacy_router.post("/api/procurement-audit/upload")
async def legacy_upload_documentation(request: Request, file: UploadFile = File(...)):
    """Совместимость со старым фронтендом."""
    return await upload_documentation(request, file)


@legacy_router.get("/api/procurement-audit/{audit_id}")
async def legacy_get_audit(request: Request, audit_id: str):
    _check_access(request)
    data = get_by_audit_id(audit_id)
    if not data:
        raise HTTPException(404, "Документ не найден или кэш устарел")
    sid = get_session_id(request)
    if data.get("filename"):
        set_documentation(sid, audit_id, data["filename"])
    return summary_for_client(data)
