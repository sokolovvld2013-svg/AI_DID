"""Единый клиент LLM: GigaChat и DeepSeek."""
import logging
import threading
from abc import ABC, abstractmethod

from core.llm_errors import LLMUserFacingError, friendly_llm_error_message
from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    GIGACHAT_CREDENTIALS,
    GIGACHAT_SCOPE,
)
from core.settings import get_selected_model, model_provider

logger = logging.getLogger(__name__)


class BaseLLM(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> str:
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
    ) -> str:
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
        return response.choices[0].message.content


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
    ) -> str:
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
        return response.choices[0].message.content


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
        logger.debug("LLM generate, model=%s, prompt length=%d", self.model, len(prompt))
        try:
            return self._backend.generate(prompt, system_prompt, context)
        except LLMUserFacingError:
            raise
        except Exception as e:
            logger.exception("LLM generate failed")
            raise LLMUserFacingError(friendly_llm_error_message(e), e) from e

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
