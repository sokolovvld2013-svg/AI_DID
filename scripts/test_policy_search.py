"""Диагностика поиска по Положению о закупке."""
from procurement.kb_rag import get_policy_rag
from lawyer.router import _select_relevant_hits

q = "Какой Срок подачи заявок при аукционе"
rag = get_policy_rag()
print("files:", rag.list_files(), "count:", rag._collection.count())
hits = rag.search(q)
print("candidates:", len(hits))
for h in hits[:10]:
    t = (h.get("text") or "")[:140].replace("\n", " ")
    print(
        f"score={h.get('score', 0):.3f} sem={h.get('semantic_score', 0):.3f} "
        f"kw={h.get('keyword_score', 0):.1f} core={h.get('core_matches')} | {t}"
    )
picked = _select_relevant_hits(q, hits)
print("picked:", len(picked))
for h in picked[:5]:
    print("---")
    print((h.get("text") or "")[:400])
