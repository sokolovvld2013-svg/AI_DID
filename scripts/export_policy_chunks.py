import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from procurement.kb_rag import get_policy_rag

rag = get_policy_rag()
n = rag._collection.count()
out = []
for off in range(0, n, 200):
    try:
        p = rag._collection.get(include=["documents", "metadatas"], limit=200, offset=off)
    except TypeError:
        p = rag._collection.get(include=["documents", "metadatas"], limit=n)
    for doc, meta in zip(p.get("documents") or [], p.get("metadatas") or []):
        if not doc:
            continue
        low = doc.lower()
        if "15.11" in doc or "15.12" in doc or "пятнадцат" in low or "15 календар" in low:
            out.append(f"page {meta.get('page')}\n{doc}\n{'=' * 40}")

Path("logs/policy_chunks.txt").write_text("\n".join(out), encoding="utf-8")
print("written", len(out))
