"""Отбор цитат для отображения пользователю."""

import re

_CITE_RE = re.compile(r"\[(\d+)\]")
MAX_DISPLAY_CITATIONS = 5


def parse_cited_fragment_ids(answer: str) -> set[int]:
    return {int(n) for n in _CITE_RE.findall(answer or "") if n.isdigit()}


def select_citations_for_display(
    answer: str,
    citations: list[dict],
    *,
    max_items: int = MAX_DISPLAY_CITATIONS,
) -> list[dict]:
    """Оставить только фрагменты, на которые ссылается ответ [N]."""
    if not citations:
        return []
    cited = parse_cited_fragment_ids(answer)
    if cited:
        chosen = [c for c in citations if c.get("id") in cited]
        chosen.sort(key=lambda c: int(c.get("id") or 0))
        return chosen[:max_items]
    return citations[:max_items]
