"""Логи пользователей: кто спрашивал, с какого IP, сколько токенов ушло, чем закончилось.

Хранилище — по одному JSONL-файлу на день в ``logs/user_logs/``: одна строка =
одна запись. Такой формат переживает переносы строк и кавычки в вопросе
(в отличие от CSV) и легко разбирается построчно. Файлы старше
``USER_LOGS_RETENTION_DAYS`` (по умолчанию 30) удаляются при записи и при старте.

Идентификатор пользователя (``login``) заложен на будущее — сейчас входа по
логинам нет, поэтому поле пишется пустым.
"""

from __future__ import annotations

import json
import logging
import contextvars
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from config import USER_LOGS_DIR, USER_LOGS_RETENTION_DAYS
from core.app_time import now_app

logger = logging.getLogger(__name__)

_FILE_DATE_FMT = "%Y-%m-%d"


# Коды ошибок для лога пользователей. В лог пишем код, а не текст для
# пользователя: текст меняется вместе с формулировками и не годится для
# группировки. Описание нужно администратору — держим его тут, чтобы UI
# не разъезжался с бэкендом.
ERROR_CODES: dict[str, str] = {
    # общие
    "EMPTY_QUESTION": "Пустой вопрос",
    "INTERNAL_ERROR": "Внутренняя ошибка",
    # языковая модель
    "LLM_ERROR": "Ошибка языковой модели",
    "LLM_CONTEXT_TOO_LONG": "Запрос не помещается в контекст модели",
    "LLM_RATE_LIMIT": "Превышен лимит запросов к модели",
    "LLM_TIMEOUT": "Таймаут ответа модели",
    "LLM_AUTH_ERROR": "Нет доступа к модели (ключ/права)",
    "LLM_UNREACHABLE": "Модель недоступна по сети",
    # документы / база знаний
    "NO_DOCUMENT": "Документ не загружен",
    "DOC_STALE": "Документ устарел, нужен повторный загруз",
    "DOC_PARSE_FAILED": "Не удалось разобрать документ",
    "KB_EMPTY": "База знаний пуста",
    "NO_RELEVANT_HITS": "Нет подходящих фрагментов в документах",
    # Экономист (n8n)
    "N8N_NOT_CONFIGURED": "Не настроен адрес n8n в .env",
    "N8N_UNREACHABLE": "n8n недоступен",
    "N8N_TIMEOUT": "Таймаут ответа n8n",
    "N8N_HTTP_ERROR": "n8n вернул ошибку",
    "N8N_EMPTY_RESPONSE": "n8n вернул пустой ответ",
    # Секретарь (аудио)
    "AUDIO_TRANSCRIBE_FAILED": "Не удалось распознать речь",
    "AUDIO_INVALID": "Некорректный аудиофайл",
    "PROTOCOL_BUILD_FAILED": "Не удалось собрать протокол",
}

# Защита от гонки: uvicorn обрабатывает запросы в одном процессе, но логи
# пишутся и из фоновых задач. Один lock на запись — дешевле, чем риск
# interleaved-записей в JSONL.
_write_lock = threading.Lock()

# Очистка при записи запускается не чаще раза в сутки на процесс: иначе каждый
# запрос сканировал бы каталог. Поэтому сервер, который не перезапускался дольше
# окна хранения, всё равно один раз в день подчищает старые файлы.
_last_cleanup_day: str = ""


def _today_str() -> str:
    return now_app().strftime(_FILE_DATE_FMT)


def _ensure_dir() -> None:
    USER_LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _cleanup_once_per_day() -> None:
    """Подчищает старые файлы не чаще раза в сутки на процесс."""
    global _last_cleanup_day
    today = _today_str()
    if _last_cleanup_day == today:
        return
    _last_cleanup_day = today
    cleanup_old_logs()


def cleanup_old_logs(retention_days: int | None = None) -> int:
    """Удаляет файлы логов старше окна хранения. Возвращает число удалённых."""
    days = retention_days if retention_days is not None else USER_LOGS_RETENTION_DAYS
    if days <= 0:
        return 0
    try:
        if not USER_LOGS_DIR.is_dir():
            return 0
        cutoff = now_app().date() - timedelta(days=days)
        removed = 0
        for path in USER_LOGS_DIR.glob("*.jsonl"):
            # Имя файла — дата (YYYY-MM-DD); файлы не по нашей схеме не трогаем.
            try:
                file_date = datetime.strptime(path.stem, _FILE_DATE_FMT).date()
            except ValueError:
                continue
            if file_date < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    logger.warning("Не удалось удалить старый лог %s", path.name)
        return removed
    except Exception:
        logger.exception("Ошибка очистки старых логов пользователей")
        return 0


def log_event(
    *,
    module: str,
    question: str,
    tokens: int,
    status: str,
    login: str = "",
    ip: str = "",
    error_code: str = "",
    duration_ms: int | None = None,
) -> None:
    """Дописывает одну запись в сегодняшний лог. Ошибки логирования не роняют запрос."""
    try:
        record: dict[str, Any] = {
            "ts": now_app().isoformat(timespec="seconds"),
            "login": login or "",
            "ip": ip or "",
            "module": module,
            "question": question or "",
            "tokens": int(tokens or 0),
            "status": status,
        }
        if error_code:
            record["error_code"] = error_code
        if duration_ms is not None:
            record["duration_ms"] = int(duration_ms)

        _ensure_dir()
        _cleanup_once_per_day()
        path = USER_LOGS_DIR / f"{_today_str()}.jsonl"
        line = json.dumps(record, ensure_ascii=False)
        with _write_lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        logger.exception("Не удалось записать лог пользователя (%s)", module)


def read_logs(
    limit: int | None = 200,
    module: str = "",
    status: str = "",
    days: int | None = None,
) -> list[dict[str, Any]]:
    """Читает последние записи из файлов окна хранения (новые сверху).

    ``limit=None`` означает «без ограничения» — выбор «все» в интерфейсе.
    """
    try:
        if not USER_LOGS_DIR.is_dir():
            return []
        window = days if days is not None else USER_LOGS_RETENTION_DAYS
        cutoff = now_app().date() - timedelta(days=max(0, window))

        rows: list[dict[str, Any]] = []
        for path in sorted(USER_LOGS_DIR.glob("*.jsonl"), reverse=True):
            try:
                file_date = datetime.strptime(path.stem, _FILE_DATE_FMT).date()
            except ValueError:
                continue
            if window > 0 and file_date < cutoff:
                continue
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(row, dict):
                            rows.append(row)
            except OSError:
                logger.warning("Не удалось прочитать лог %s", path.name)

        # Файлы уже отсортированы от новых к старым, но внутри файла записи
        # идут по возрастанию — разворачиваем весь набор, чтобы получить
        # хронологический порядок «свежие сверху».
        rows.reverse()

        if module:
            rows = [r for r in rows if r.get("module") == module]
        if status:
            rows = [r for r in rows if r.get("status") == status]

        if limit is None:
            return rows
        return rows[: max(0, limit)]
    except Exception:
        logger.exception("Ошибка чтения логов пользователей")
        return []


def modules() -> list[str]:
    """Список модулей, которые пишут логи (для фильтра в интерфейсе)."""
    return ["economist", "lawyer", "procurement", "secretary", "tenders"]


# Логин пользователя заложен на будущее: сейчас входа по логинам нет.
# Когда появится аутентификация, сюда начнёт подставляться реальный логин.
def get_login(request: Any = None) -> str:
    """Логин пользователя. Входа по логинам пока нет — поле заложено на будущее."""
    if request is None:
        return ""
    user = getattr(getattr(request, "state", None), "user", None)
    if user is None:
        return ""
    login = getattr(user, "login", None) or getattr(user, "username", None) or ""
    return str(login or "")


def get_ip(request: Any) -> str:
    """IP клиента. Приложение ходит напрямую (127.0.0.1 или адрес в сети)."""
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client else None
    return str(host or "")


# Текущий запрос: нужен, чтобы писать лог из внутренних функций роутера,
# куда request не передаётся. Ставится middleware на каждый запрос.
_current_request: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "user_logs_request", default=None
)


def set_current_request(request: Any) -> None:
    _current_request.set(request)


def log_query(
    request: Any = None,
    *,
    module: str,
    question: str,
    status: str,
    tokens: int = 0,
    error_code: str = "",
) -> None:
    """Запись результата обработки вопроса: логин, IP, вопрос, токены, статус.

    Если request не передан, берётся текущий запрос из контекста.
    """
    req = request if request is not None else _current_request.get()
    log_event(
        module=module,
        question=question,
        tokens=tokens,
        status=status,
        login=get_login(req),
        ip=get_ip(req) if req is not None else "",
        error_code=error_code,
    )
