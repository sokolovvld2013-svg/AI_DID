"""Идентификатор сессии браузера (cookie) — для личной истории запросов."""
import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

SESSION_COOKIE = "did_sid"
SESSION_MAX_AGE = 365 * 24 * 60 * 60
_SID_RE = re.compile(r"^[a-f0-9]{32}$")


def get_session_id(request: Request) -> str:
    """ID сессии текущего браузера (устанавливается SessionMiddleware)."""
    sid = getattr(request.state, "session_id", None)
    if isinstance(sid, str) and _SID_RE.match(sid):
        return sid
    cookie = request.cookies.get(SESSION_COOKIE, "")
    if _SID_RE.match(cookie):
        return cookie
    return uuid.uuid4().hex


class SessionMiddleware(BaseHTTPMiddleware):
    """Выдаёт cookie did_sid при первом визите; история привязана к нему."""

    async def dispatch(self, request: Request, call_next):
        sid = request.cookies.get(SESSION_COOKIE, "")
        if not _SID_RE.match(sid):
            sid = uuid.uuid4().hex
            request.state.session_id = sid
            response = await call_next(request)
            response.set_cookie(
                SESSION_COOKIE,
                sid,
                max_age=SESSION_MAX_AGE,
                httponly=True,
                samesite="lax",
                path="/",
            )
            return response
        request.state.session_id = sid
        return await call_next(request)
