"""API-роутер модуля Экономист (факт в Google-таблице, чат через n8n)."""

import logging



from fastapi import APIRouter, HTTPException, Request

from fastapi.responses import HTMLResponse

from fastapi.templating import Jinja2Templates

from pydantic import BaseModel



from config import (

    BASE_DIR,

    ECONOMIST_FACT_SHEET_EDIT_URL,

    N8N_ECONOMIST_WEBHOOK_METHOD,
    N8N_ECONOMIST_WEBHOOK_URL,

)

from core.history import economist_history
from core.session import get_session_id

from economist.n8n_client import (
    _is_meaningless_text,
    ask_economist_n8n,
    is_test_webhook_url,
)



logger = logging.getLogger(__name__)

ECONOMIST_CHAT_ERROR = "Произошла ошибка или отсутствуют данные! Попробуйте переформулировать вопрос."

router = APIRouter(prefix="/economist", tags=["economist"])

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))





class QueryRequest(BaseModel):

    message: str


def _economist_error_reply(query: str, session_id: str) -> dict:
    """Ответ в чат при ошибке n8n или пустом ответе (без HTTP 502)."""
    economist_history.add(
        session_id,
        query,
        ECONOMIST_CHAT_ERROR,
        intent="error",
        html="",
        render="text",
    )
    return {
        "answer": ECONOMIST_CHAT_ERROR,
        "records": [],
        "html": "",
        "render": "text",
        "intent": "error",
    }


@router.get("", response_class=HTMLResponse)

async def economist_page(request: Request):

    sid = get_session_id(request)
    return templates.TemplateResponse(

        request=request,

        name="economist.html",

        context={

            "active": "economist",

            "history": economist_history.list(sid),

            "fact_sheet_url": ECONOMIST_FACT_SHEET_EDIT_URL,

        },

    )





@router.get("/status")

async def status():

    return {

        "fact_sheet_url": ECONOMIST_FACT_SHEET_EDIT_URL or None,

        "fact_sheet_configured": bool(ECONOMIST_FACT_SHEET_EDIT_URL),

        "n8n_configured": bool(N8N_ECONOMIST_WEBHOOK_URL),
        "n8n_webhook_method": N8N_ECONOMIST_WEBHOOK_METHOD,
        "n8n_test_webhook": is_test_webhook_url(N8N_ECONOMIST_WEBHOOK_URL),
    }





@router.post("/query")

async def query(req: QueryRequest, request: Request):

    message = req.message.strip()
    sid = get_session_id(request)

    if not message:

        raise HTTPException(400, "Пустой запрос")



    if not N8N_ECONOMIST_WEBHOOK_URL:
        logger.warning("N8N_ECONOMIST_WEBHOOK_URL не задан")
        return _economist_error_reply(message, sid)

    try:
        response_text, records, table_html = await ask_economist_n8n(
            message,
            fact_sheet_url=ECONOMIST_FACT_SHEET_EDIT_URL,
        )

        if not isinstance(response_text, str):
            if isinstance(response_text, (list, dict)):
                from economist.n8n_client import finalize_economist_response, render_economist_html

                response_text, records, report = finalize_economist_response(response_text)
                table_html = render_economist_html(records, report)
            else:
                response_text = str(response_text or "")

        response_text = response_text.strip()

        if not response_text and records:
            from economist.n8n_client import _format_article_record

            response_text = "\n\n".join(_format_article_record(r) for r in records)

        if not table_html and records:
            from economist.n8n_client import render_economist_html

            table_html = render_economist_html(records)

    except ValueError as e:
        logger.warning("Ошибка запроса к n8n: %s", e)
        return _economist_error_reply(message, sid)

    except Exception as e:
        logger.exception("Ошибка запроса к n8n: %s", e)
        return _economist_error_reply(message, sid)

    if not response_text and not records and not table_html:
        logger.warning("n8n вернул пустой ответ для запроса: %s", message[:80])
        return _economist_error_reply(message, sid)

    if _is_meaningless_text(response_text) and not records and not table_html:
        logger.warning("n8n вернул пустой ответ (%r) для запроса: %s", response_text, message[:80])
        return _economist_error_reply(message, sid)



    economist_history.add(
        sid,
        message,
        response_text,
        intent="n8n",
        html=table_html,
        render="table" if table_html else "text",
    )

    return {
        "answer": response_text,
        "records": records,
        "html": table_html,
        "render": "table" if table_html else "text",
        "intent": "n8n",
    }





@router.get("/history")

async def history(request: Request):

    return {"history": economist_history.list(get_session_id(request))}


