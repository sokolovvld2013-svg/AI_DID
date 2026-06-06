"""Скачать модель эмбеддингов в папку models/ (для работы без доступа к huggingface.co)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv

load_dotenv(BASE / ".env")

MODEL_ID = os.getenv(
    "LOCAL_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
).strip()
if Path(MODEL_ID).is_dir():
    MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

OUT_DIR = BASE / "models" / MODEL_ID.split("/")[-1]


def main() -> None:
    endpoint = os.getenv("HF_ENDPOINT", "https://hf-mirror.com").strip()
    if endpoint:
        os.environ["HF_ENDPOINT"] = endpoint.rstrip("/")
        print(f"HF_ENDPOINT={endpoint}")

    timeout = os.getenv("HF_HUB_DOWNLOAD_TIMEOUT", "300")
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = timeout
    print(f"Скачивание {MODEL_ID}")
    print(f"→ {OUT_DIR}")

    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=MODEL_ID,
        local_dir=str(OUT_DIR),
        local_dir_use_symlinks=False,
    )

    print("\nГотово. Добавьте в .env:")
    print(f"LOCAL_EMBEDDING_MODEL={OUT_DIR}")
    print("EMBEDDING_LOCAL_FILES_ONLY=1")
    print("EMBEDDING_PROVIDER=local")
    print("\nУстановите зависимости: pip install -r requirements-local-embeddings.txt")


if __name__ == "__main__":
    main()
