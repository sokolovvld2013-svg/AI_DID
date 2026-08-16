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
        if "13.16" in doc or "13.17" in doc or "13.18" in doc or "13.19" in doc or "13.20" in doc:
            lines.append(f"page {meta.get('page')}\n{doc}\n{'='*40}")

Path("logs/policy_13x.txt").write_text("\n".join(lines), encoding="utf-8")
print("sections", len(lines))
