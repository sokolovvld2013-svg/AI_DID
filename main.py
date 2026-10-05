"""Точка входа FastAPI — ИИ-помощник ФГУП «ДИД»."""
import logging
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import (
    AUTH_ENABLED,
    BASE_DIR,
    CHROMA_PERSIST_DIR,
    FAVICON_SOURCE,
    KAD_AGENT_ENABLED,
    LAWYER_UPLOAD_DIR,
    LOGO_SOURCE,
    PROCUREMENT_CACHE_DIR,
    PROCUREMENT_POLICY_UPLOAD_DIR,
    PROCUREMENT_UPLOAD_DIR,
    SECRETARY_UPLOAD_DIR,
    STATIC_FAVICON,
    STATIC_LOGO,
    TENDERS_CACHE_DIR,
    TENDERS_UPLOAD_DIR,
    WHISPER_PRELOAD,
)
from core.auth import auth_status
from core.auth_router import AuthMiddleware
from core.auth_router import router as auth_router
from core.session import SessionMiddleware
from core.settings import get_company_name
from core.settings_router import router as settings_router
from core.user_logs import cleanup_old_logs, set_current_request
from economist.router import router as economist_router
from lawyer.arbitr.agent_api import router as kad_agent_router
from lawyer.arbitr.router import router as kad_router
from lawyer.doc_processor import docx_available, pymupdf_available
from lawyer.router import router as lawyer_router
from procurement.router import legacy_router
from procurement.router import router as procurement_router
from secretary.anomizer.src.anomizer import router as anomizer_router
from secretary.router import router as secretary_router
from tenders.router import router as tenders_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _ensure_dirs():
    for d in (
        SECRETARY_UPLOAD_DIR,
        LAWYER_UPLOAD_DIR,
        PROCUREMENT_UPLOAD_DIR,
        PROCUREMENT_POLICY_UPLOAD_DIR,
        PROCUREMENT_CACHE_DIR,
        TENDERS_UPLOAD_DIR,
        TENDERS_CACHE_DIR,
        CHROMA_PERSIST_DIR,
        BASE_DIR / "static",
        BASE_DIR / "static" / "img",
        BASE_DIR / "templates",
        BASE_DIR / "logs",
    ):
        d.mkdir(parents=True, exist_ok=True)


def _copy_asset_if_needed(src, dst, label: str) -> None:
    """Копирует файл в static/img, если источник другой (не тот же путь)."""
    if not src.is_file():
        logger.warning("Файл %s не найден: %s", label, src)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dst.resolve():
        return
    shutil.copy2(src, dst)
    logger.info("%s обновлён: %s", label.capitalize(), dst)


def _ensure_logo():
    """Копирует логотип в static/img для отдачи через StaticFiles."""
    _copy_asset_if_needed(LOGO_SOURCE, STATIC_LOGO, "логотип")


def _ensure_favicon():
    """Копирует фавиконку в static/img для отдачи через StaticFiles."""
    _copy_asset_if_needed(FAVICON_SOURCE, STATIC_FAVICON, "фавиконка")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_dirs()
    _ensure_logo()
    _ensure_favicon()
    if not pymupdf_available():
        logger.warning(
            "Модуль Юрист: не установлен pymupdf — многие PDF не прочитаются. "
            "Выполните: venv\\Scripts\\pip install pymupdf pdfplumber"
        )
    if not docx_available():
        logger.warning(
            "Модуль Юрист: не установлен python-docx — загрузка DOCX недоступна. "
            "Выполните: pip install python-docx"
        )
    if WHISPER_PRELOAD:
        try:
            from secretary.transcriber import preload_model

            preload_model()
        except Exception as e:
            logger.warning("Предзагрузка Whisper не удалась: %s", e)
    try:
        from secretary.anomizer.src.anomizer.availability import (
            missing_dependencies,
            unavailable_message,
        )

        _missing = missing_dependencies()
        if _missing:
            logger.warning(
                "Модуль Секретарь (обезличивание) недоступен — /api/anonymize "
                "вернёт 503. %s",
                unavailable_message(_missing),
            )
    except Exception as e:
        logger.warning("Проверка модуля обезличивания не удалась: %s", e)
    try:
        _removed = cleanup_old_logs()
        logger.info("Очистка логов пользователей: удалено старых файлов — %s", _removed)
    except Exception as e:
        logger.warning("Очистка логов пользователей не удалась: %s", e)
    _log_auth_status()
    logger.info("Приложение запущено")
    yield
    logger.info("Приложение остановлено")


def _log_auth_status() -> None:
    """Состояние входа при старте: кого пускаем и чем это кончится."""
    if not AUTH_ENABLED:
        logger.warning("Вход по логину и паролю выключен (AUTH_ENABLED=false) — приложение доступно всем")
        return
    try:
        status = auth_status()
    except Exception as e:
        logger.warning("Не удалось прочитать файл пользователей: %s", e)
        return
    if not status["users"]:
        logger.error(
            "Вход включён, но пользователей нет (%s) — все страницы вернут 503. "
            "Создайте администратора: python -m scripts.manage_users add <логин> --admin",
            status["users_file"],
        )
        return
    logger.info(
        "Вход включён: пользователей — %s, администраторов — %s, срок сессии — %s с",
        status["users"],
        status["admins"],
        status["session_max_age"],
    )
    if not status["cookie_secure"]:
        logger.warning(
            "AUTH_COOKIE_SECURE=false: cookie сессии уходит и по HTTP. "
            "Включите HTTPS и AUTH_COOKIE_SECURE=true, иначе пароль передаётся открыто"
        )


app = FastAPI(
    title=f'ИИ-помощник {get_company_name()}',
    description="Модули: Экономист, Юрист, Закупка, Торги, Секретарь",
    lifespan=lifespan,
)

@app.middleware("http")
async def user_logs_middleware(request: Request, call_next):
    """Кладёт текущий запрос в контекст, чтобы писать логи из кода роутеров,
    и сбрасывает счётчик токенов LLM в начале каждого запроса."""
    from core.llm_client import reset_usage

    set_current_request(request)
    reset_usage()
    return await call_next(request)


app.add_middleware(SessionMiddleware)
# AuthMiddleware добавляется последним — значит, выполняется первым и успевает
# положить request.state.user до остальных middleware и роутеров.
app.add_middleware(AuthMiddleware)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(auth_router)
app.include_router(economist_router)
app.include_router(secretary_router)
app.include_router(lawyer_router)
app.include_router(procurement_router)
app.include_router(tenders_router)
app.include_router(legacy_router)
app.include_router(anomizer_router)
app.include_router(settings_router)

# Парсер КАД: маршруты появляются только при KAD_AGENT_ENABLED. Пока фича
# выключена, её нет вовсе - ни вкладки, ни 404-заглушек, ни в OpenAPI.
if KAD_AGENT_ENABLED:
    app.include_router(kad_router)
    app.include_router(kad_agent_router)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """Проверка живости для мониторинга. Открыт без входа (см. PUBLIC_PATHS)
    и ничего не сообщает о состоянии настроек."""
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    if STATIC_FAVICON.is_file():
        return FileResponse(STATIC_FAVICON, media_type="image/png")
    return RedirectResponse(url="/static/img/favicon.png", status_code=302)


@app.get("/")
async def root():
    return RedirectResponse(url="/economist", status_code=302)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    logger.exception("Необработанная ошибка: %s %s", request.url, exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
