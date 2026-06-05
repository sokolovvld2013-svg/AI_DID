"""API-роутер модуля Юрист."""
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from config import (
    ALLOWED_DOC_EXT,
    BASE_DIR,
    LAWYER_BALANCE_FILES,
    LAWYER_SEMANTIC_MIN_SCORE,
    LAWYER_UPLOAD_DIR,
    MAX_DOCUMENT_SIZE,
    MAX_LAWYER_CITATION_CHARS,
    MAX_LAWYER_LLM_CONTEXT_CHARS,
)
from core.history import lawyer_history
from core.llm_errors import LLMUserFacingError
from core.llm_client import get_llm
from lawyer.doc_processor import process_upload
from lawyer.rag import LawyerRAG, MIN_CITATION_SCORE_RATIO
from lawyer.search_utils import core_query_tokens, min_core_matches_required
from lawyer.citation_repair import repair_citation_display
from lawyer.citations import select_citations_for_display
from lawyer.text_encoding import decode_upload_filename, repair_citation_text, repair_text, strip_urls

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/lawyer", tags=["lawyer"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

_rag = LawyerRAG()

def _safe_stored_name(original: str, ext: str) -> str:
    """Безопасное имя файла с сохранением расширения (.pdf не обрезается)."""
    stem = re.sub(r"[^\w.\-]", "_", Path(original).stem)[:80]
    if not stem:
        stem = "document"
    return f"{stem}{ext}"


SYSTEM_PROMPT = """Ты — юридический ассистент. Отвечай на основе нумерованных фрагментов [1], [2], … ниже.

Правила:
- Если во фрагменте есть слова из вопроса (или близкие по смыслу) — извлеки из него ответ по теме вопроса.
- Не пиши «информация отсутствует», если хотя бы один фрагмент содержит связанные с вопросом термины.
- Не выдумывай факты вне текста фрагментов.
- В ответе обязательно указывай номера использованных фрагментов: [1], [2] (только те, на которые опираешься).
- Отвечай на русском языке, кратко и по существу."""


class LawyerQuery(BaseModel):
    question: str


def _truncate_fragment(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _lawyer_error_reply(question: str, message: str) -> dict:
    lawyer_history.add(question, message)
    return {"answer": message, "citations": []}


def _select_relevant_hits(question: str, hits: list[dict]) -> list[dict]:
    """Фрагменты для LLM и источников: только с достаточной релевантностью запросу."""
    from lawyer.rag import CONTEXT_K

    if not hits:
        return []

    core = core_query_tokens(question)
    need_core = min_core_matches_required(core)
    best_score = float(hits[0].get("score") or 0)
    min_score = max(0.1, best_score * MIN_CITATION_SCORE_RATIO)

    def _is_relevant(hit: dict) -> bool:
        score = float(hit.get("score") or 0)
        kw = float(hit.get("keyword_score") or 0)
        cm = int(hit.get("core_matches") or 0)
        sem = float(hit.get("semantic_score") or 0)

        stem = float(hit.get("stem_score") or 0)
        if sem >= LAWYER_SEMANTIC_MIN_SCORE:
            return True
        if sem >= 0.38 and score >= min_score * 0.45:
            return True
        if stem >= 28 or float(hit.get("phrase_score") or 0) >= 20:
            return True
        if core and cm >= len(core) and stem >= 15:
            return True
        if cm >= need_core and (score >= min_score or kw >= 4.0):
            return True
        if len(core) >= 2 and cm >= 2 and kw >= 3.0:
            return True
        if cm >= 1 and kw >= 6.0:
            return True
        if not core and (score >= 0.28 or sem >= 0.52):
            return True
        if score >= min_score and sem >= 0.42 and cm >= 1:
            return True
        return False

    picked = [h for h in hits if _is_relevant(h)]

    # Несколько документов: опционально добавить лучшие с каждого файла
    file_ids_in_hits = {h.get("file_id") for h in hits if h.get("file_id")}

    if LAWYER_BALANCE_FILES and len(file_ids_in_hits) > 1:
        picked_ids = {h.get("file_id") for h in picked}
        min_per_file = max(1, CONTEXT_K // len(file_ids_in_hits))
        if CONTEXT_K >= 4:
            min_per_file = max(2, min_per_file)

        by_file: dict[str, list[dict]] = {}
        for h in hits:
            fid = h.get("file_id") or ""
            if fid:
                by_file.setdefault(fid, []).append(h)

        diversified: list[dict] = []
        seen_keys: set[str] = set()

        def _hit_uid(h: dict) -> str:
            return f"{h.get('file_id')}_{h.get('chunk_index')}"

        for fid in sorted(file_ids_in_hits):
            pool = sorted(
                by_file.get(fid) or [],
                key=lambda h: (
                    h.get("phrase_score", 0),
                    h.get("stem_score", 0),
                    h.get("core_matches", 0),
                    h.get("score", 0),
                ),
                reverse=True,
            )
            added = 0
            for h in pool:
                if added >= min_per_file:
                    break
                if not _is_relevant(h):
                    continue
                uid = _hit_uid(h)
                if uid in seen_keys:
                    continue
                diversified.append(h)
                seen_keys.add(uid)
                added += 1

        for h in picked:
            uid = _hit_uid(h)
            if uid not in seen_keys:
                diversified.append(h)
                seen_keys.add(uid)
        picked = diversified

        picked_ids = {h.get("file_id") for h in picked}
        for h in hits:
            if len(picked) >= CONTEXT_K:
                break
            fid = h.get("file_id")
            if not fid or fid in picked_ids:
                continue
            if (
                h.get("core_matches", 0) >= 1
                or float(h.get("keyword_score") or 0) >= 2.0
                or float(h.get("score") or 0) >= min_score * 0.7
            ):
                picked.append(h)
                picked_ids.add(fid)

    if not picked and hits:
        sem_floor = max(0.35, LAWYER_SEMANTIC_MIN_SCORE * 0.85)
        fallback = [
            h
            for h in hits[:8]
            if int(h.get("core_matches") or 0) >= 1
            or float(h.get("keyword_score") or 0) >= 2.0
            or float(h.get("semantic_score") or 0) >= sem_floor
        ]
        picked = fallback[: max(3, min(CONTEXT_K, len(fallback)))] if fallback else hits[: min(3, CONTEXT_K)]
        logger.info(
            "Поиск «%s»: мягкий отбор, фрагментов=%d (best score=%.3f)",
            question[:40],
            len(picked),
            best_score,
        )
    elif not picked:
        logger.info("Поиск «%s»: нет релевантных фрагментов (best score=%.3f)", question[:40], best_score)
        return []

    return picked[:CONTEXT_K]


@router.get("", response_class=HTMLResponse)
async def lawyer_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="lawyer.html",
        context={
            "active": "lawyer",
            "history": lawyer_history.list(),
            "files": _rag.list_files(),
        },
    )


@router.get("/files")
async def list_files():
    return {"files": _rag.list_files()}


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_DOC_EXT:
        raise HTTPException(400, f"Допустимы: {ALLOWED_DOC_EXT}")

    content = await file.read()
    if len(content) > MAX_DOCUMENT_SIZE:
        raise HTTPException(400, f"Файл превышает {MAX_DOCUMENT_SIZE // (1024*1024)} МБ")

    if ext == ".pdf" and not content.startswith(b"%PDF"):
        raise HTTPException(400, "Файл не является корректным PDF")

    LAWYER_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    orig_name = decode_upload_filename(file.filename) or f"document{ext}"
    if not Path(orig_name).suffix:
        orig_name = f"{orig_name}{ext}"
    stored_name = _safe_stored_name(orig_name, ext)
    temp_path = LAWYER_UPLOAD_DIR / f"temp_{uuid.uuid4().hex[:10]}_{stored_name}"

    try:
        temp_path.write_bytes(content)
        logger.info(
            "Загружен %s: %s (%d байт) → %s",
            ext,
            orig_name,
            len(content),
            temp_path.name,
        )
        file_id, chunks = process_upload(temp_path, orig_name)
        dest = LAWYER_UPLOAD_DIR / f"{file_id}_{stored_name}"
        temp_path.rename(dest)
        count = _rag.add_chunks(chunks)
        if count == 0:
            raise ValueError("Не удалось проиндексировать документ (пустые фрагменты)")
    except ValueError as e:
        if temp_path.exists():
            logger.error("Не удалось обработать документ, файл сохранён: %s", temp_path)
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        logger.exception("Ошибка эмбеддингов при загрузке")
        raise HTTPException(503, str(e)) from e
    except Exception as e:
        logger.exception("Ошибка обработки документа")
        raise HTTPException(500, f"Ошибка: {e}") from e

    return {
        "status": "ok",
        "file_id": file_id,
        "filename": orig_name,
        "chunks": count,
    }


@router.delete("/files/{file_id}")
async def delete_file(file_id: str):
    if not _rag.delete_file(file_id):
        raise HTTPException(404, "Файл не найден")
    return {"status": "ok", "file_id": file_id}


@router.delete("/index")
async def clear_index():
    _rag.clear_all()
    return {"status": "ok", "message": "База знаний очищена"}


@router.post("/query")
async def query(req: LawyerQuery):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Пустой вопрос")

    all_hits = _rag.search(question)
    if not all_hits:
        answer = "База знаний пуста. Загрузите документы для ответа на вопросы."
        lawyer_history.add(question, answer)
        return {"answer": answer, "citations": []}

    try:
        hits = _select_relevant_hits(question, all_hits)
        if not hits:
            answer = (
                "По загруженным документам не найдено фрагментов, подходящих к вопросу. "
                "Переформулируйте вопрос или загрузите другой документ."
            )
            lawyer_history.add(question, answer)
            return {"answer": answer, "citations": []}

        llm = get_llm()

        context_parts: list[str] = []
        citations: list[dict] = []
        context_len = 0

        for i, hit in enumerate(hits, 1):
            merged_text = _rag.merge_neighbor_context(hit)
            raw_text = _truncate_fragment(
                strip_urls(repair_citation_text(merged_text or hit.get("text") or "")),
                MAX_LAWYER_CITATION_CHARS,
            )
            filename = strip_urls(repair_text(hit["filename"] or ""))
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
                "text": strip_urls(repair_citation_display(raw_text, llm)),
                "filename": filename,
                "page": hit["page"],
                "file_id": hit["file_id"],
            })

        context = "\n\n".join(context_parts)
        raw_answer = llm.generate(
            f"Вопрос пользователя: {question}\n\n"
            "Используй только фрагменты ниже. Если в них есть слова из вопроса — "
            "дай ответ по их содержанию и укажи номера [N].",
            system_prompt=SYSTEM_PROMPT,
            context=context,
        )

        answer = strip_urls(repair_text(raw_answer))
        citations = select_citations_for_display(answer, citations)
        if not citations and hits:
            logger.info("В ответе нет ссылок [N] — источники не показаны")
        lawyer_history.add(question, answer, citations=citations)
        return {"answer": answer, "citations": citations}
    except LLMUserFacingError as e:
        logger.warning("Lawyer query LLM error: %s", e.original or e)
        return _lawyer_error_reply(question, e.user_message)
    except Exception as e:
        logger.exception("Lawyer query failed: %s", e)
        return _lawyer_error_reply(
            question,
            "Произошла ошибка при обработке запроса. Попробуйте переформулировать вопрос.",
        )


@router.get("/history")
async def history():
    repaired = []
    for item in lawyer_history.list():
        entry = dict(item)
        entry["query"] = repair_text(entry.get("query") or "")
        entry["response"] = strip_urls(repair_text(entry.get("response") or ""))
        if entry.get("citations"):
            entry["citations"] = [
                {
                    **c,
                    "text": strip_urls(repair_citation_text(c.get("text") or "")),
                    "filename": strip_urls(repair_text(c.get("filename") or "")),
                }
                for c in entry["citations"]
            ]
        repaired.append(entry)
    return {"history": repaired}
