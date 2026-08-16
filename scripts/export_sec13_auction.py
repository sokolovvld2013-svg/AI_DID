import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from procurement.kb_rag import get_policy_rag

rag = get_policy_rag()
n = rag._collection.count()
lines = []
for off in range(0, n, 200):
    try:
        p = rag._collection.get(include=["documents", "metadatas"], limit=200, offset=off)
    except TypeError:
        p = rag._collection.get(include=["documents", "metadatas"], limit=n)
    for doc, meta in zip(p.get("documents") or [], p.get("metadatas") or []):
        if not doc:
            continue
        page = meta.get("page")
        if page in (48, 49, 50) and "13." in doc:
            lines.append(f"page {page}\n{doc}\n{'='*40}")

Path("logs/policy_sec13_auction.txt").write_text("\n".join(lines), encoding="utf-8")
print("chunks", len(lines))
