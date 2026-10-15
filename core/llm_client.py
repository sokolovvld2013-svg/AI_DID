"""Единый клиент LLM: GigaChat и DeepSeek."""
import contextvars
import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    GIGACHAT_CREDENTIALS,
    GIGACHAT_SCOPE,
)
from core.llm_errors import LLMUserFacingError, friendly_llm_error_message
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


@dataclass
class LLMUsage:
    """Расход токенов стримингового вызова.

    В потоковой генерации счётчики приходят в последнем чанке провайдера, а
    сам стрим читается из потоков диспетчера — contextvars между ними не
    переносится. Поэтому расход накапливается здесь и передаётся явно
    (в log_query(tokens=...)), а не через contextvar.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class BaseLLM(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> LLMResult:
        pass

    def stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> tuple[Iterator[str], LLMUsage]:
        """Потоковая генерация: куски текста + накопленный расход токенов.

        Заглушка по умолчанию — одним куском из generate(). Бэкенды с
        нативным стримингом (GigaChat, DeepSeek) переопределяют метод и
        отдают текст кусками по мере генерации.
        """
        result = self.generate(prompt, system_prompt, context)
        usage = LLMUsage(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
        )

        def _single() -> Iterator[str]:
            yield result.text

        return _single(), usage


def _build_user_content(prompt: str, system_prompt: str | None, context: str | None) -> str:
    """Единая сборка содержимого сообщения для GigaChat (один блок «Система»)."""
    parts = []
    if system_prompt:
        parts.append(f"Система: {system_prompt}")
    if context:
        parts.append(f"Контекст:\n{context}")
    parts.append(f"Запрос:\n{prompt}")
    return "\n\n".join(parts)


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

        user_content = _build_user_content(prompt, system_prompt, context)

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

    def stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> tuple[Iterator[str], LLMUsage]:
        from gigachat.models import Chat, Messages, MessagesRole

        user_content = _build_user_content(prompt, system_prompt, context)
        usage = LLMUsage()

        def _chunks() -> Iterator[str]:
            # stream=True в payload не нужен: клиент сам добавляет его в SSE-запрос.
            for chunk in self._client.stream(
                Chat(
                    messages=[Messages(role=MessagesRole.USER, content=user_content)],
                    model=self._model,
                )
            ):
                # Счётчики приходят в последнем чанке (ChatUsage: input/output).
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage.prompt_tokens = int(getattr(chunk_usage, "input_tokens", 0) or 0)
                    usage.completion_tokens = int(getattr(chunk_usage, "output_tokens", 0) or 0)
                    usage.total_tokens = int(getattr(chunk_usage, "total_tokens", 0) or 0) or (
                        usage.prompt_tokens + usage.completion_tokens
                    )
                for choice in chunk.choices or []:
                    text = getattr(getattr(choice, "delta", None), "content", None)
                    if text:
                        yield text

        return _chunks(), usage


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

    def stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> tuple[Iterator[str], LLMUsage]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        user_parts = []
        if context:
            user_parts.append(f"Контекст:\n{context}")
        user_parts.append(prompt)
        messages.append({"role": "user", "content": "\n\n".join(user_parts)})

        usage = LLMUsage()

        def _chunks() -> Iterator[str]:
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.3,
                stream=True,
                # API DeepSeek присылает счётчики токенов в последнем чанке.
                stream_options={"include_usage": True},
            )
            for chunk in stream:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage.prompt_tokens = int(getattr(chunk_usage, "prompt_tokens", 0) or 0)
                    usage.completion_tokens = int(getattr(chunk_usage, "completion_tokens", 0) or 0)
                    usage.total_tokens = int(getattr(chunk_usage, "total_tokens", 0) or 0) or (
                        usage.prompt_tokens + usage.completion_tokens
                    )
                # Финальный чанк с include_usage приходит без choices.
                if not chunk.choices:
                    continue
                text = chunk.choices[0].delta.content
                if text:
                    yield text

        return _chunks(), usage


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

    def stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> tuple[Iterator[str], LLMUsage]:
        """Потоковая генерация для длинных ответов.

        Куски текста отдаются по мере генерации: браузер получает первые
        байты сразу, и длинный ответ не упирается в таймаут обратного
        прокси (504 Gateway Time-out). Считать расход токенов через
        contextvar здесь нельзя — стрим читается из потоков диспетчера, —
        поэтому он накапливается в LLMUsage и передаётся явно.
        """
        logger.debug("LLM stream, model=%s, prompt length=%d", self.model, len(prompt))
        chunks, usage = self._backend.stream(prompt, system_prompt, context)
        return self._safe_stream(chunks), usage

    def _safe_stream(self, chunks: Iterator[str]) -> Iterator[str]:
        """Чтение чанков с переводом ошибок API в понятные пользователю."""
        try:
            yield from chunks
        except LLMUserFacingError:
            raise
        except Exception as e:
            logger.exception("LLM stream failed")
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
