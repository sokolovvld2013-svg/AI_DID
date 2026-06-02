"""Единый клиент LLM: GigaChat и DeepSeek."""
import logging
from abc import ABC, abstractmethod

from core.llm_errors import LLMUserFacingError, friendly_llm_error_message
from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    GIGACHAT_CREDENTIALS,
    GIGACHAT_MODEL,
    GIGACHAT_SCOPE,
    LLM_PROVIDER,
)

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
    def __init__(self):
        from gigachat import GigaChat

        self._client = GigaChat(
            credentials=GIGACHAT_CREDENTIALS,
            scope=GIGACHAT_SCOPE,
            verify_ssl_certs=False,
        )
        self._model = GIGACHAT_MODEL

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

        response = self._client.chat(
            Chat(
                messages=[
                    Messages(role=MessagesRole.USER, content=user_content),
                ],
                model=self._model,
            )
        )
        return response.choices[0].message.content


class DeepSeekLLM(BaseLLM):
    def __init__(self):
        from openai import OpenAI

        self._client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        self._model = DEEPSEEK_MODEL

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
    """Фасад для переключения провайдера через конфиг."""

    def __init__(self, provider: str | None = None):
        provider = (provider or LLM_PROVIDER).lower()
        if provider == "gigachat":
            self._backend: BaseLLM = GigaChatLLM()
        elif provider == "deepseek":
            self._backend = DeepSeekLLM()
        else:
            raise ValueError(f"Неизвестный LLM_PROVIDER: {provider}")

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> str:
        logger.debug("LLM generate, prompt length=%d", len(prompt))
        try:
            return self._backend.generate(prompt, system_prompt, context)
        except LLMUserFacingError:
            raise
        except Exception as e:
            logger.exception("LLM generate failed")
            raise LLMUserFacingError(friendly_llm_error_message(e), e) from e


_llm: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm
