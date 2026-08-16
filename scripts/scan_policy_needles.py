import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from procurement.kb_rag import get_policy_rag

rag = get_policy_rag()
n = rag._collection.count()
needles = ["не менее", "13.5", "13.6", "13.7", "13.8", "13.9", "13.10", "аукцион в электрон", "срок подачи заявок"]
for needle in needles:
    hits = []
    for off in range(0, n, 200):
        try:
            p = rag._collection.get(include=["documents", "metadatas"], limit=200, offset=off)
        except TypeError:
            p = rag._collection.get(include=["documents", "metadatas"], limit=n)
        for doc, meta in zip(p.get("documents") or [], p.get("metadatas") or []):
            if doc and needle.lower() in doc.lower():
                hits.append((meta.get("page"), doc[:250]))
    print(needle, len(hits))
    for pg, snip in hits[:2]:
        print(" ", pg, snip.replace("\n", " ")[:180])
