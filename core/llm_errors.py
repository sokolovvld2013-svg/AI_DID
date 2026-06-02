"""Понятные сообщения об ошибках языковых моделей для пользователя."""

import logging
import re

logger = logging.getLogger(__name__)

_GENERIC = (
    "Не удалось получить ответ от языковой модели. "
    "Попробуйте переформулировать вопрос или повторите запрос позже."
)


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
    blob = _error_blob(exc)

    if "max_tokens_per_request" in blob or re.search(
        r"requested\s+\d+\s+tokens.*max\s+\d+\s+tokens", blob
    ):
        return (
            "Запрос слишком большой для модели (превышен лимит токенов). "
            "Задайте более конкретный вопрос или удалите лишние документы из базы."
        )
    if "context length" in blob or "maximum context" in blob or "too many tokens" in blob:
        return (
            "Слишком много текста в запросе для модели. "
            "Сократите вопрос или уменьшите число загруженных документов."
        )
    if "rate limit" in blob or "429" in blob:
        return "Слишком много запросов к модели. Подождите немного и повторите."
    if "timeout" in blob or "timed out" in blob:
        return "Превышено время ожидания ответа модели. Повторите запрос."
    if "api key" in blob or "authentication" in blob or "401" in blob or "403" in blob:
        return "Ошибка доступа к языковой модели. Проверьте ключ API в настройках сервера."
    if "connection" in blob or "network" in blob or "connect" in blob:
        return "Нет связи с сервисом языковой модели. Проверьте подключение и повторите запрос."

    logger.debug("LLM error without specific mapping: %s", exc)
    return _GENERIC
