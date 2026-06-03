"""Точка входа FastAPI — ИИ-помощник ФГУП «ДИД»."""
import logging
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import (
    BASE_DIR,
    CHROMA_PERSIST_DIR,
    ECONOMIST_UPLOAD_DIR,
    FAVICON_SOURCE,
    LAWYER_UPLOAD_DIR,
    LOGO_SOURCE,
    SECRETARY_UPLOAD_DIR,
    STATIC_FAVICON,
    STATIC_LOGO,
)
from economist.router import router as economist_router
from lawyer.doc_processor import pymupdf_available
from lawyer.router import router as lawyer_router
from secretary.router import router as secretary_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _ensure_dirs():
    for d in (
        ECONOMIST_UPLOAD_DIR,
        SECRETARY_UPLOAD_DIR,
        LAWYER_UPLOAD_DIR,
        CHROMA_PERSIST_DIR,
        BASE_DIR / "static",
        BASE_DIR / "static" / "img",
        BASE_DIR / "templates",
    ):
        d.mkdir(parents=True, exist_ok=True)


def _ensure_logo():
    """Копирует логотип в static/img для отдачи через StaticFiles."""
    if not LOGO_SOURCE.is_file():
        logger.warning("Файл логотипа не найден: %s", LOGO_SOURCE)
        return
    
    # Проверка: если пути одинаковые - пропускаем
    if LOGO_SOURCE.resolve() == STATIC_LOGO.resolve():
        return
    
    STATIC_LOGO.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LOGO_SOURCE, STATIC_LOGO)
    logger.info("Логотип обновлён: %s", STATIC_LOGO)


def _ensure_favicon():
    """Копирует favicon в static/img для отдачи через StaticFiles."""
    if not FAVICON_SOURCE.is_file():
        logger.warning("Файл favicon не найден: %s", FAVICON_SOURCE)
        return
    
    # Проверка: если пути одинаковые - пропускаем
    if FAVICON_SOURCE.resolve() == STATIC_FAVICON.resolve():
        return
    
    STATIC_FAVICON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FAVICON_SOURCE, STATIC_FAVICON)
    logger.info("Favicon обновлён: %s", STATIC_FAVICON)


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
    logger.info("Приложение запущено")
    yield
    logger.info("Приложение остановлено")


app = FastAPI(
    title='ИИ-помощник ФГУП "ДИД"',
    description="Модули: Экономист, Секретарь, Юрист",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(economist_router)
app.include_router(secretary_router)
app.include_router(lawyer_router)


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
