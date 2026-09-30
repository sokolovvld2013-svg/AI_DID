"""Понятные сообщения об ошибках языковых моделей для пользователя."""

import logging
import re

logger = logging.getLogger(__name__)

_GENERIC = (
    "Не удалось получить ответ от языковой модели. "
    "Попробуйте переформулировать вопрос или повторите запрос позже."
)

# Тексты для пользователя по кодам из llm_error_code(). Логи содержат только
# код, поэтому этот словарь — единственное место, где живёт формулировка.
_ERROR_TEXT: dict[str, str] = {
    "LLM_CONTEXT_TOO_LONG": (
        "Запрос слишком большой для модели (превышен лимит токенов). "
        "Задайте более конкретный вопрос или удалите лишние документы из базы."
    ),
    "LLM_RATE_LIMIT": "Слишком много запросов к модели. Подождите немного и повторите.",
    "LLM_TIMEOUT": "Превышено время ожидания ответа модели. Повторите запрос.",
    "LLM_AUTH_ERROR": "Ошибка доступа к языковой модели. Проверьте ключ API в настройках сервера.",
    "LLM_UNREACHABLE": "Нет связи с сервисом языковой модели. Проверьте подключение и повторите запрос.",
    "LLM_ERROR": _GENERIC,
}


class LLMUserFacingError(Exception):
    """Ошибка LLM с текстом для показа пользователю."""

    def __init__(self, user_message: str, original: BaseException | None = None):
        self.user_message = user_message
        super().__init__(user_message)
        self.original = original


def _error_blob(exc: BaseException) -> str:
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body is not None:
        parts.append(str(body))
    if exc.__cause__:
        parts.append(str(exc.__cause__))
    return " ".join(parts).lower()


def friendly_llm_error_message(exc: BaseException) -> str:
    """Краткое сообщение на русском по типу ошибки API."""
    return _ERROR_TEXT.get(llm_error_code(exc), _GENERIC)


def llm_error_code(exc: BaseException) -> str:
    """Короткий код ошибки для логов — по той же классификации, что и текст."""
    blob = _error_blob(exc)

    if "max_tokens_per_request" in blob or re.search(
        r"requested\s+\d+\s+tokens.*max\s+\d+\s+tokens", blob
    ):
        return "LLM_CONTEXT_TOO_LONG"
    if "context length" in blob or "maximum context" in blob or "too many tokens" in blob:
        return "LLM_CONTEXT_TOO_LONG"
    if "rate limit" in blob or "429" in blob:
        return "LLM_RATE_LIMIT"
    if "timeout" in blob or "timed out" in blob:
        return "LLM_TIMEOUT"
    if "api key" in blob or "authentication" in blob or "401" in blob or "403" in blob:
        return "LLM_AUTH_ERROR"
    if "connection" in blob or "network" in blob or "connect" in blob:
        return "LLM_UNREACHABLE"

    logger.debug("LLM error without specific mapping: %s", exc)
    return "LLM_ERROR"
