"""Вход и выход: страница входа, обработка формы, служебные API для интерфейса.

Здесь же middleware, который закрывает приложение, и зависимости
``require_user`` / ``require_admin`` для точечной защиты маршрутов.
"""
from __future__ import annotations

import logging
import time
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from config import AUTH_ADMIN_ONLY_SETTINGS, AUTH_COOKIE, AUTH_ENABLED
from core.auth import (
    AuthError,
    AuthUnavailable,
    Principal,
    auth_status,
    clear_auth_cookie,
    clear_failures,
    get_user,
    lockout_remaining,
    read_token,
    register_failure,
    set_auth_cookie,
    set_password,
    throttle_key,
    verify_credentials,
)
from core.session import get_session_id
from core.templates import templates
from core.user_logs import get_ip

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# Куда отправлять после входа и куда возвращать неавторизованного.
HOME_URL = "/economist"
LOGIN_URL = "/login"
PASSWORD_URL = "/account/password"
# Пути, минующие проверку входа: форма входа и файлы оформления.
# Сравнение — точное или с префиксом «/» (то есть и /static/css/style.css).
PUBLIC_PATHS = frozenset({LOGIN_URL, "/favicon.ico", "/static", "/healthz"})
# Обрабатывают вход и выход сами, поэтому middleware их не перекрывает.
AUTH_PATHS = frozenset({"/api/login", "/api/logout", "/logout"})
# Пока пароль временный, доступно только то, что нужно для его смены.
PASSWORD_PATHS = frozenset({PASSWORD_URL, "/api/auth/password", "/api/auth/me", "/logout", "/api/logout"})
# На эти пути нельзя вернуться после входа: иначе получится кольцо редиректов.
_NO_REDIRECT_AFTER_LOGIN = frozenset({LOGIN_URL, "/logout"})

NO_USERS_HINT = (
    "Вход включён, но пользователей нет. Создайте администратора командой "
    "на сервере: python -m scripts.manage_users add <логин> --admin"
)


class LoginPayload(BaseModel):
    login: str
    password: str
    next: str = ""


# --------------------------------------------------------------------- кто вошёл

def current_user(request: Request) -> Principal | None:
    """Кто вошёл, по подписанной cookie (или None)."""
    if not AUTH_ENABLED:
        return None
    return read_token(request.cookies.get(AUTH_COOKIE, ""))


def require_user(request: Request) -> Principal:
    """Зависимость FastAPI: пускает только вошедших."""
    if not AUTH_ENABLED:
        # Вход выключен — маршрут доступен всем, прав администратора нет.
        return Principal(login="", role="user")
    principal = current_user(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Требуется вход")
    return principal


def require_admin(request: Request) -> Principal:
    """Зависимость FastAPI: только роль admin."""
    principal = require_user(request)
    if AUTH_ENABLED and AUTH_ADMIN_ONLY_SETTINGS and not principal.is_admin:
        raise HTTPException(status_code=403, detail="Нужны права администратора")
    return principal


def _safe_next(raw: str) -> str:
    """Куда вернуть после входа.

    Только путь внутри приложения: ``next=//host`` увёл бы на другой сайт,
    а ``next=/logout`` или ``/login`` зациклили бы редиректы.
    """
    target = (raw or "").strip()
    if not target.startswith("/") or target.startswith("//"):
        return HOME_URL
    if target.split("?", 1)[0] in _NO_REDIRECT_AFTER_LOGIN:
        return HOME_URL
    return target


def _wants_html(request: Request) -> bool:
    """Браузер ждёт страницу, клиент API — JSON. Путь /api/ всегда JSON."""
    if request.url.path.startswith("/api/"):
        return False
    return "text/html" in request.headers.get("accept", "")


def _fail(request: Request, message: str, status_code: int = 401):
    """Единый ответ на отказ: страница с текстом ошибки или JSON."""
    if _wants_html(request):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": message,
                "login": "",
                "next": _safe_next(request.query_params.get("next", "")),
                "no_users": False,
            },
            status_code=status_code,
        )
    return JSONResponse({"detail": message}, status_code=status_code)


# ------------------------------------------------------------------ вход и выход

@router.get(LOGIN_URL, response_class=HTMLResponse)
async def login_page(request: Request, next: str = ""):
    """Страница входа. Уже вошедшего сразу отправляем на нужный модуль."""
    if not AUTH_ENABLED:
        return RedirectResponse(url=_safe_next(next), status_code=302)
    if not auth_status()["users"]:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "", "login": "", "next": _safe_next(next), "no_users": True},
            status_code=503,
        )
    if current_user(request) is not None:
        return RedirectResponse(url=_safe_next(next), status_code=302)
    return templates.TemplateResponse(
        request, "login.html", {"error": "", "login": "", "next": _safe_next(next), "no_users": False}
    )


@router.post(LOGIN_URL)
async def login_submit(
    request: Request,
    login: str = Form(default=""),
    password: str = Form(default=""),
    next: str = Form(default=""),
):
    """Проверка формы входа."""
    return _process_login(request, login, password, next)


@router.post("/api/login")
async def api_login(request: Request, payload: LoginPayload):
    """Вход для клиентов, которые шлют JSON, а не браузерную форму."""
    return _process_login(request, payload.login, payload.password, payload.next, force_json=True)


def _process_login(
    request: Request,
    login: str,
    password: str,
    next: str = "",
    force_json: bool = False,
):
    """Общая часть входа: блокировка, проверка пароля, выдача cookie."""
    if not AUTH_ENABLED:
        return JSONResponse({"detail": "Вход выключен"}, status_code=400)

    login = (login or "").strip()
    ip = get_ip(request)
    key = throttle_key(login, ip)

    wait = lockout_remaining(key)
    if wait:
        logger.warning("Вход заблокирован на %s с (IP %s)", wait, ip)
        return _fail(
            request,
            f"Слишком много неудачных попыток. Повторите через {wait} с",
            status_code=429,
        )

    try:
        record = verify_credentials(login, password)
    except AuthError:
        # Некорректный формат логина — отвечаем так же, как на неверный пароль.
        record = None
    except AuthUnavailable as e:
        logger.error("Вход недоступен: %s", e)
        return _fail(request, "Вход временно недоступен", status_code=503)

    if record is None:
        left = register_failure(key)
        logger.warning(
            "Неудачный вход: логин=%r IP=%s%s",
            login,
            ip,
            f" (блокировка на {left} с)" if left else "",
        )
        if left:
            return _fail(
                request,
                f"Слишком много неудачных попыток. Повторите через {left} с",
                status_code=429,
            )
        return _fail(request, "Неверный логин или пароль")

    clear_failures(key)
    principal = Principal(
        login=record.login,
        role=record.role,
        session_id=get_session_id(request),
        issued_at=time.time(),
    )
    target = PASSWORD_URL if record.must_change_password else _safe_next(next)
    logger.info("Вход: %s (роль %s, IP %s)", principal.login, principal.role, ip)

    # Cookie ставится на тот ответ, который реально уходит: у 303-редиректа и
    # у JSON-ответа это разные объекты.
    if force_json:
        response = JSONResponse({"login": record.login, "role": record.role, "next": target})
    else:
        # 303: после входа браузер обязан перейти на GET.
        response = RedirectResponse(url=target, status_code=303)
    set_auth_cookie(response, principal)
    return response


@router.post("/logout")
@router.get("/logout")
async def logout(request: Request):
    """Выход: cookie удаляется, браузер возвращается на страницу входа.

    Всегда редирект — этим маршрутом пользуется только форма в шапке.
    Программным клиентам нужен /api/logout с JSON-ответом.
    """
    principal = current_user(request)
    if principal is not None:
        logger.info("Выход: %s", principal.login)
    response = RedirectResponse(url=LOGIN_URL, status_code=303)
    clear_auth_cookie(response)
    return response


@router.post("/api/logout")
async def api_logout(request: Request):
    """Выход для клиентов API."""
    principal = current_user(request)
    if principal is not None:
        logger.info("Выход: %s", principal.login)
    response = JSONResponse({"detail": "Вы вышли"})
    clear_auth_cookie(response)
    return response


@router.get(PASSWORD_URL, response_class=HTMLResponse)
async def change_password_page(request: Request, reason: str = ""):
    """Страница смены пароля — в том числе при входе по временному паролю."""
    principal = current_user(request)
    if not AUTH_ENABLED:
        return RedirectResponse(url=HOME_URL, status_code=302)
    if principal is None:
        return RedirectResponse(url=f"{LOGIN_URL}?next={PASSWORD_URL}", status_code=303)
    if not principal.must_change_password and not reason:
        # Пароль свой, менять необязательно — возвращаем назад.
        return RedirectResponse(url=_safe_next(request.query_params.get("next", "")), status_code=302)
    return templates.TemplateResponse(
        request,
        "change_password.html",
        {"user": principal, "forced": principal.must_change_password, "error": reason},
    )


class PasswordPayload(BaseModel):
    current_password: str
    new_password: str


@router.post("/api/auth/password")
async def change_password(request: Request, payload: PasswordPayload):
    """Смена собственного пароля. Ранее выданные сессии после неё не работают."""
    principal = current_user(request)
    record = get_user(principal.login) if principal else None
    if record is None:
        raise HTTPException(status_code=401, detail="Требуется вход")
    if verify_credentials(record.login, payload.current_password) is None:
        register_failure(throttle_key(record.login, get_ip(request)))
        raise HTTPException(status_code=400, detail="Текущий пароль неверен")
    try:
        set_password(record.login, payload.new_password, must_change_password=False)
    except AuthError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Токен выдан до смены пароля, поэтому уже недействителен — нужен новый вход.
    response = JSONResponse({"detail": "Пароль изменён, войдите заново"})
    clear_auth_cookie(response)
    return response


@router.get("/api/auth/me")
async def auth_me(request: Request):
    """Кто вошёл — интерфейсу и для проверки прав."""
    principal = current_user(request)
    record = get_user(principal.login) if principal else None
    return {
        "enabled": AUTH_ENABLED,
        "authenticated": principal is not None,
        "user": principal.public() if principal else None,
        "must_change_password": bool(record.must_change_password) if record else False,
        "settings_admin_only": AUTH_ADMIN_ONLY_SETTINGS,
        "status": auth_status(),
    }


# --------------------------------------------------------------------- middleware

class AuthMiddleware(BaseHTTPMiddleware):
    """Закрывает приложение: без входа проходят только форма входа и статика.

    Вход выключен (``AUTH_ENABLED=false``) — проверок нет. Включён, но
    пользователей нет — 503 с подсказкой вместо пуска: иначе после неудачного
    развёртывания приложение оказалось бы открыто всем.
    """

    async def dispatch(self, request: Request, call_next):
        principal = current_user(request)
        request.state.user = principal

        if not AUTH_ENABLED or _is_public(request.url.path) or request.url.path in AUTH_PATHS:
            return await call_next(request)

        if principal is None:
            if not auth_status()["users"]:
                return self._no_users(request)
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Требуется вход"}, status_code=401)
            return RedirectResponse(
                url=f"{LOGIN_URL}?next={quote(_current_target(request), safe='/?&=%:+~$,;@!*()')}",
                status_code=303,
            )

        # Вход по временному паролю: пускаем только на смену пароля и на выход.
        if principal.must_change_password and not _matches(request.url.path, PASSWORD_PATHS):
            if request.url.path.startswith("/api/"):
                return JSONResponse(
                    {"detail": "Сначала смените пароль", "next": PASSWORD_URL},
                    status_code=403,
                )
            return RedirectResponse(url=PASSWORD_URL, status_code=303)

        return await call_next(request)

    @staticmethod
    def _no_users(request: Request):
        logger.error("%s", NO_USERS_HINT)
        if request.url.path.startswith("/api/") or not _wants_html(request):
            return JSONResponse({"detail": NO_USERS_HINT}, status_code=503)
        return HTMLResponse(_no_users_page(), status_code=503)


def _is_public(path: str) -> bool:
    return _matches(path, PUBLIC_PATHS)


def _matches(path: str, paths) -> bool:
    """Точное совпадение или путь внутри: /logout и /logout/anything."""
    return any(path == p or path.startswith(f"{p}/") for p in paths)


def _current_target(request: Request) -> str:
    """Текущий адрес с запросом — вернуть сюда после входа."""
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return target


def _no_users_page() -> str:
    """Заглушка на случай «включили вход, но не создали пользователей»."""
    from markupsafe import escape

    return (
        '<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        "<title>Вход не настроен</title>"
        '<link rel="stylesheet" href="/static/css/style.css"></head>'
        '<body><main class="main"><div class="login-card">'
        '<h1 class="login-title">Вход не настроен</h1>'
        f'<p class="settings-error">{escape(NO_USERS_HINT)}</p>'
        "</div></main></body></html>"
    )
