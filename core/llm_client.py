"""Единый клиент LLM: GigaChat и DeepSeek."""
import contextvars
import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

from core.llm_errors import LLMUserFacingError, friendly_llm_error_message
from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    GIGACHAT_CREDENTIALS,
    GIGACHAT_SCOPE,
)
from core.settings import get_selected_model, model_provider

logger = logging.getLogger(__name__)


@dataclass
class LLMResult:
    """Ответ LLM вместе с расходом токенов по этому вызову."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def build(
        cls,
        text: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> "LLMResult":
        """Собирает результат: total при отсутствии считается как сумма частей."""
        total = int(total_tokens or 0) or (int(prompt_tokens or 0) + int(completion_tokens or 0))
        return cls(
            text=text,
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            total_tokens=total,
        )


# Сумма токенов по всем LLM-вызовам в рамках одного запроса пользователя.
# Один вопрос может породить несколько вызовов (перефразирование, сборка
# ответа), и в лог пишется суммарный расход. contextvars изолируют счётчик
# между параллельными запросами.
_usage_tokens: contextvars.ContextVar[int] = contextvars.ContextVar(
    "llm_usage_tokens", default=0
)


def reset_usage() -> None:
    """Обнулить счётчик токенов в начале обработки запроса."""
    _usage_tokens.set(0)


def current_usage() -> int:
    """Сколько токенов израсходовано с последнего reset_usage()."""
    return _usage_tokens.get()


def _account(result: LLMResult) -> LLMResult:
    _usage_tokens.set(_usage_tokens.get() + result.total_tokens)
    return result


class BaseLLM(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> LLMResult:
        pass


class GigaChatLLM(BaseLLM):
    def __init__(self, model: str):
        from gigachat import GigaChat

        self._client = GigaChat(
            credentials=GIGACHAT_CREDENTIALS,
            scope=GIGACHAT_SCOPE,
            verify_ssl_certs=False,
        )
        self._model = model

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> LLMResult:
        from gigachat.models import Chat, Messages, MessagesRole

        parts = []
        if system_prompt:
            parts.append(f"Система: {system_prompt}")
        if context:
            parts.append(f"Контекст:\n{context}")
        parts.append(f"Запрос:\n{prompt}")
        user_content = "\n\n".join(parts)

        # gigachat 0.2+ (в requirements.txt) ждёт модель внутри Chat.
        response = self._client.chat(
            Chat(
                messages=[Messages(role=MessagesRole.USER, content=user_content)],
                model=self._model,
            )
        )
        usage = getattr(response, "usage", None)
        return LLMResult.build(
            text=response.choices[0].message.content,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
        )


class DeepSeekLLM(BaseLLM):
    def __init__(self, model: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        self._model = model

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> LLMResult:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        user_parts = []
        if context:
            user_parts.append(f"Контекст:\n{context}")
        user_parts.append(prompt)
        messages.append({"role": "user", "content": "\n\n".join(user_parts)})

        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=0.3,
        )
        usage = getattr(response, "usage", None)
        return LLMResult.build(
            text=response.choices[0].message.content,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
        )


class LLMClient:
    """Фасад: провайдер и модель берутся из настроек приложения."""

    def __init__(self, provider: str | None = None, model: str | None = None):
        model = model or get_selected_model()
        provider = (provider or model_provider(model)).lower()
        if provider == "gigachat":
            self._backend: BaseLLM = GigaChatLLM(model)
        elif provider == "deepseek":
            self._backend = DeepSeekLLM(model)
        else:
            raise ValueError(f"Неизвестный LLM_PROVIDER: {provider}")

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> str:
        """Возвращает текст ответа; расход токенов учитывается в счётчике.

        Наружу отдаётся строка, чтобы не трогать все места вызова. Фактический
        расход смотрится через current_usage() в конце обработки запроса.
        """
        logger.debug("LLM generate, model=%s, prompt length=%d", self.model, len(prompt))
        try:
            result = self._backend.generate(prompt, system_prompt, context)
        except LLMUserFacingError:
            raise
        except Exception as e:
            logger.exception("LLM generate failed")
            raise LLMUserFacingError(friendly_llm_error_message(e), e) from e
        _account(result)
        logger.debug(
            "LLM usage: prompt=%d, completion=%d",
            result.prompt_tokens,
            result.completion_tokens,
        )
        return result.text

    @property
    def model(self) -> str:
        return self._backend._model


_llm: LLMClient | None = None
_llm_key: tuple[str, str] | None = None
_llm_lock = threading.Lock()


def get_llm() -> LLMClient:
    """Клиент для выбранной в настройках модели (создаётся заново при смене)."""
    global _llm, _llm_key
    model = get_selected_model()
    key = (model_provider(model), model)
    with _llm_lock:
        if _llm is None or _llm_key != key:
            _llm = LLMClient(provider=key[0], model=model)
            _llm_key = key
            logger.info("Активная LLM: провайдер=%s, модель=%s", key[0], model)
        return _llm
