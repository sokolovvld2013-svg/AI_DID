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
        low = doc.lower()
        if "заяв" in low and "15" in doc and "аукцион" in low:
            lines.append(f"page {meta.get('page')}\n{doc}\n{'='*40}")
        elif "срок" in low and "подач" in low and "15" in doc:
            lines.append(f"page {meta.get('page')}\n{doc}\n{'='*40}")

Path("logs/policy_15_deadline.txt").write_text("\n".join(lines), encoding="utf-8")
print("matches", len(lines))
