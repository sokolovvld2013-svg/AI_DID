"""RAG для Положения о закупке (отдельная коллекция Chroma)."""

from config import CHROMA_PERSIST_DIR, PROCUREMENT_POLICY_UPLOAD_DIR
from lawyer.rag import LawyerRAG

PROCUREMENT_COLLECTION = "procurement_kb"

_rag: LawyerRAG | None = None


def get_policy_rag() -> LawyerRAG:
    global _rag
    if _rag is None:
        _rag = LawyerRAG(
            collection_name=PROCUREMENT_COLLECTION,
            upload_dir=PROCUREMENT_POLICY_UPLOAD_DIR,
            persist_dir=CHROMA_PERSIST_DIR,
        )
    return _rag
