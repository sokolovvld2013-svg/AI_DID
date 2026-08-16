import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from procurement.kb_rag import get_policy_rag

rag = get_policy_rag()
n = rag._collection.count()
matches = []
for off in range(0, n, 200):
    try:
        p = rag._collection.get(include=["documents", "metadatas"], limit=200, offset=off)
    except TypeError:
        p = rag._collection.get(include=["documents", "metadatas"], limit=n)
    for doc, meta in zip(p.get("documents") or [], p.get("metadatas") or []):
        if not doc:
            continue
        low = doc.lower()
        if "аукцион" in low and ("календар" in low or "15" in doc):
            matches.append((meta.get("page"), doc))

lines = []
for page, doc in matches:
    lines.append(f"page {page}\n{doc}\n{'='*40}")
Path("logs/policy_auction.txt").write_text("\n".join(lines), encoding="utf-8")
print("auction+calendar matches:", len(matches))

# also search exact phrase variants
for needle in ["не менее 15", "15 календар", "пятнадцать) календар", "15 (пят"]:
    c = 0
    for off in range(0, n, 200):
        try:
            p = rag._collection.get(include=["documents"], limit=200, offset=off)
        except TypeError:
            p = rag._collection.get(include=["documents"], limit=n)
        for doc in p.get("documents") or []:
            if doc and needle in doc.lower():
                c += 1
    print(needle, c)
