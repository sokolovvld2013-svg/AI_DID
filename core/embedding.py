"""Получение эмбеддингов: локальная модель или OpenAI API."""
import logging
import os
from abc import ABC, abstractmethod
from typing import List

import numpy as np

from config import (
    EMBEDDING_LOCAL_FILES_ONLY,
    EMBEDDING_PROVIDER,
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
        from sentence_transformers import SentenceTransformer

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
        except Exception as e:
            raise RuntimeError(
                "Не удалось загрузить модель эмбеддингов.\n"
                "• Быстро: в .env укажите EMBEDDING_PROVIDER=openai (нужен OPENAI_API_KEY).\n"
                "• Офлайн: scripts\\download_embedding_model.bat, затем EMBEDDING_LOCAL_FILES_ONLY=1.\n"
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
        resp = self._client.embeddings.create(input=prepared, model=self._model)
        return [item.embedding for item in resp.data]

    def embed_query(self, text: str) -> List[float]:
        return self.embed([text])[0]


_embedder: BaseEmbedder | None = None


def get_embedder() -> BaseEmbedder:
    global _embedder
    if _embedder is None:
        if EMBEDDING_PROVIDER == "openai":
            if not OPENAI_API_KEY:
                raise RuntimeError("EMBEDDING_PROVIDER=openai, но OPENAI_API_KEY не задан")
            _embedder = OpenAIEmbedder()
        else:
            _embedder = LocalEmbedder()
    return _embedder
