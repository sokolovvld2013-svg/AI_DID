"""Получение эмбеддингов: локальная модель, OpenAI API или GigaChat API."""
import logging
import os
from abc import ABC, abstractmethod
from typing import List

import numpy as np

from config import (
    EMBEDDING_LOCAL_FILES_ONLY,
    EMBEDDING_PROVIDER,
    GIGACHAT_CREDENTIALS,
    GIGACHAT_EMBEDDING_MODEL,
    GIGACHAT_MAX_EMBED_CHARS,
    GIGACHAT_MAX_EMBED_TOKENS,
    GIGACHAT_SCOPE,
    LOCAL_EMBEDDING_MODEL,
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL,
    configure_huggingface_env,
)

logger = logging.getLogger(__name__)

MAX_EMBED_CHARS = int(os.getenv("MAX_EMBED_CHARS", "6000"))
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "16"))


class BaseEmbedder(ABC):
    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        pass

    @abstractmethod
    def embed_query(self, text: str) -> List[float]:
        pass


def _prepare_texts(texts: List[str]) -> List[str]:
    """Нормализация текстов перед encode (пустые строки ломают sentence-transformers)."""
    prepared: list[str] = []
    for raw in texts:
        s = str(raw or "").replace("\x00", " ").strip()
        if not s:
            s = "."
        if len(s) > MAX_EMBED_CHARS:
            s = s[:MAX_EMBED_CHARS]
        prepared.append(s)
    return prepared


def _model_is_local_path(model_id: str) -> bool:
    if not model_id:
        return False
    if model_id.startswith(("/", ".", "\\")):
        return True
    return len(model_id) > 1 and model_id[1] == ":"


class LocalEmbedder(BaseEmbedder):
    def __init__(self):
        configure_huggingface_env()
        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError as e:
            raise RuntimeError(
                "EMBEDDING_PROVIDER=local, но sentence-transformers не установлен. "
                "Выполните: pip install -r requirements-local-embeddings.txt — "
                "или в .env укажите EMBEDDING_PROVIDER=gigachat / openai"
            ) from e

        local_only = EMBEDDING_LOCAL_FILES_ONLY or _model_is_local_path(LOCAL_EMBEDDING_MODEL)
        logger.info(
            "Загрузка локальной модели эмбеддингов: %s (local_files_only=%s)",
            LOCAL_EMBEDDING_MODEL,
            local_only,
        )
        try:
            self._model = SentenceTransformer(
                LOCAL_EMBEDDING_MODEL,
                local_files_only=local_only,
            )
        except ModuleNotFoundError as e:
            raise RuntimeError(
                "Не удалось загрузить модель эмбеддингов.\n"
                "• Быстро: в .env укажите EMBEDDING_PROVIDER=openai или gigachat.\n"
                "• Локально: pip install -r requirements-local-embeddings.txt, "
                "затем scripts\\download_embedding_model.bat и EMBEDDING_LOCAL_FILES_ONLY=1.\n"
                "• Сеть: HF_ENDPOINT=https://hf-mirror.com и HF_HUB_DOWNLOAD_TIMEOUT=300.\n"
                f"Модель: {LOCAL_EMBEDDING_MODEL}\n"
                f"Ошибка: {e}"
            ) from e
        except Exception as e:
            raise RuntimeError(
                "Не удалось загрузить модель эмбеддингов.\n"
                "• Быстро: в .env укажите EMBEDDING_PROVIDER=openai или gigachat.\n"
                "• Локально: pip install -r requirements-local-embeddings.txt, "
                "затем scripts\\download_embedding_model.bat и EMBEDDING_LOCAL_FILES_ONLY=1.\n"
                "• Сеть: HF_ENDPOINT=https://hf-mirror.com и HF_HUB_DOWNLOAD_TIMEOUT=300.\n"
                f"Модель: {LOCAL_EMBEDDING_MODEL}\n"
                f"Ошибка: {e}"
            ) from e

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        prepared = _prepare_texts(texts)
        all_rows: list[list[float]] = []

        for start in range(0, len(prepared), EMBED_BATCH_SIZE):
            batch = prepared[start : start + EMBED_BATCH_SIZE]
            if not batch:
                continue
            vectors = self._model.encode(
                batch,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            arr = np.atleast_2d(vectors)
            all_rows.extend(row.tolist() for row in arr)

        if len(all_rows) != len(prepared):
            raise RuntimeError(
                f"Модель вернула {len(all_rows)} векторов вместо {len(prepared)}"
            )
        return all_rows

    def embed_query(self, text: str) -> List[float]:
        return self.embed([text])[0]


class OpenAIEmbedder(BaseEmbedder):
    def __init__(self):
        from openai import OpenAI

        self._client = OpenAI(api_key=OPENAI_API_KEY)
        self._model = OPENAI_EMBEDDING_MODEL

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        prepared = _prepare_texts(texts)
        all_rows: list[list[float]] = []
        total = len(prepared)

        if total > EMBED_BATCH_SIZE:
            logger.info(
                "OpenAI embeddings: %d фрагментов, пакетами по %d (лимит API ~300k токенов/запрос)",
                total,
                EMBED_BATCH_SIZE,
            )

        for start in range(0, total, EMBED_BATCH_SIZE):
            batch = prepared[start : start + EMBED_BATCH_SIZE]
            resp = self._client.embeddings.create(input=batch, model=self._model)
            ordered = sorted(resp.data, key=lambda item: item.index)
            all_rows.extend(item.embedding for item in ordered)

        if len(all_rows) != total:
            raise RuntimeError(
                f"OpenAI вернул {len(all_rows)} векторов вместо {total}"
            )
        return all_rows

    def embed_query(self, text: str) -> List[float]:
        return self.embed([text])[0]


def _gigachat_chars_budget(max_chars: int, max_tokens: int) -> int:
    """Символьный лимит с запасом под кириллицу (~0.75 токена/символ у GigaChat)."""
    by_tokens = max(200, int(max_tokens / 0.78))
    return max(200, min(max_chars, by_tokens))


def _truncate_gigachat_embed_text(
    text: str,
    max_chars: int,
    max_tokens: int | None = None,
) -> str:
    """Обрезка под лимит GigaChat (~514 токенов), head+tail."""
    limit = _gigachat_chars_budget(
        max_chars,
        max_tokens if max_tokens is not None else GIGACHAT_MAX_EMBED_TOKENS,
    )
    if len(text) <= limit:
        return text
    if text.startswith("[") and "\n" in text:
        header, body = text.split("\n", 1)
        body_budget = max(160, limit - len(header) - 8)
        if len(body) <= body_budget:
            return f"{header}{body}"
        head_len = max(90, body_budget * 2 // 3)
        tail_len = max(50, body_budget - head_len - 5)
        return f"{header}{body[:head_len]}\n...\n{body[-tail_len:]}"
    head_len = max(120, (limit - 8) * 2 // 3)
    tail_len = max(50, limit - head_len - 8)
    return f"{text[:head_len]}\n...\n{text[-tail_len:]}"


def _gigachat_token_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "413" in msg or "tokens limit exceeded" in msg


class GigaChatEmbedder(BaseEmbedder):
    def __init__(self):
        from gigachat import GigaChat

        if not GIGACHAT_CREDENTIALS:
            raise RuntimeError(
                "EMBEDDING_PROVIDER=gigachat, но GIGACHAT_CREDENTIALS не задан"
            )
        self._client = GigaChat(
            credentials=GIGACHAT_CREDENTIALS,
            scope=GIGACHAT_SCOPE,
            verify_ssl_certs=False,
        )
        self._model = GIGACHAT_EMBEDDING_MODEL
        self._max_chars = GIGACHAT_MAX_EMBED_CHARS
        self._max_tokens = GIGACHAT_MAX_EMBED_TOKENS
        self._char_budget = _gigachat_chars_budget(self._max_chars, self._max_tokens)
        logger.info(
            "GigaChat embeddings: модель %s, до %d симв. (~%d токенов) на запрос",
            self._model,
            self._char_budget,
            self._max_tokens,
        )

    def _prepare_gigachat_batch(
        self,
        texts: list[str],
        char_budget: int,
        token_budget: int,
    ) -> list[str]:
        return [
            _truncate_gigachat_embed_text(s, char_budget, token_budget) for s in texts
        ]

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        resp = self._client.embeddings(texts=batch, model=self._model)
        return [item.embedding for item in resp.data]

    def _embed_one_with_fallback(self, text: str) -> list[float]:
        char_budget = self._char_budget
        token_budget = self._max_tokens
        for attempt in range(4):
            prepared = _truncate_gigachat_embed_text(text, char_budget, token_budget)
            try:
                return self._embed_batch([prepared])[0]
            except Exception as e:
                if not _gigachat_token_limit_error(e) or attempt >= 3:
                    raise
                char_budget = max(180, int(char_budget * 0.72))
                token_budget = max(360, int(token_budget * 0.85))
                logger.warning(
                    "GigaChat: лимит токенов, повтор с %d симв. (~%d токенов)",
                    char_budget,
                    token_budget,
                )
        raise RuntimeError("GigaChat: не удалось уложиться в лимит токенов")

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        raw_prepared = _prepare_texts(texts)
        prepared = self._prepare_gigachat_batch(
            raw_prepared, self._char_budget, self._max_tokens
        )
        truncated = sum(
            1 for raw, prep in zip(raw_prepared, prepared) if len(prep) < len(raw)
        )
        if truncated:
            logger.info(
                "GigaChat: укорочено %d/%d текстов до ≤%d симв. (лимит API ~514 токенов)",
                truncated,
                len(texts),
                self._char_budget,
            )
        all_rows: list[list[float]] = []
        total = len(prepared)

        if total > EMBED_BATCH_SIZE:
            logger.info(
                "GigaChat embeddings: %d фрагментов, пакетами по %d",
                total,
                EMBED_BATCH_SIZE,
            )

        for start in range(0, total, EMBED_BATCH_SIZE):
            batch = prepared[start : start + EMBED_BATCH_SIZE]
            try:
                all_rows.extend(self._embed_batch(batch))
            except Exception as e:
                if not _gigachat_token_limit_error(e):
                    raise
                logger.warning(
                    "GigaChat: пакет отклонён по лимиту токенов, по одному фрагменту"
                )
                for text in batch:
                    all_rows.append(self._embed_one_with_fallback(text))

        if len(all_rows) != total:
            raise RuntimeError(
                f"GigaChat вернул {len(all_rows)} векторов вместо {total}"
            )
        return all_rows

    def embed_query(self, text: str) -> List[float]:
        return self.embed([text])[0]


_embedder: BaseEmbedder | None = None


def get_embedder() -> BaseEmbedder:
    global _embedder
    if _embedder is None:
        if EMBEDDING_PROVIDER == "openai":
            if not OPENAI_API_KEY:
                raise RuntimeError("EMBEDDING_PROVIDER=openai, но OPENAI_API_KEY не задан")
            try:
                _embedder = OpenAIEmbedder()
            except ModuleNotFoundError:
                raise RuntimeError(
                    "EMBEDDING_PROVIDER=openai, но пакет openai не установлен. "
                    "Выполните: pip install openai — или в .env укажите EMBEDDING_PROVIDER=local"
                ) from None
        elif EMBEDDING_PROVIDER == "gigachat":
            try:
                _embedder = GigaChatEmbedder()
            except ModuleNotFoundError:
                raise RuntimeError(
                    "EMBEDDING_PROVIDER=gigachat, но пакет gigachat не установлен. "
                    "Выполните: pip install gigachat — или в .env укажите EMBEDDING_PROVIDER=local"
                ) from None
        else:
            _embedder = LocalEmbedder()
    return _embedder
