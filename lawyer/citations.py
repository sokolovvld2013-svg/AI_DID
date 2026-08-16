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
    """Оставить фрагменты, на которые ссылается ответ [N]."""
    if not citations:
        return []
    cited = parse_cited_fragment_ids(answer)
    if cited:
        id_map: dict[int, dict] = {}
        for c in citations:
            cid = c.get("id")
            if cid is not None:
                id_map[int(cid)] = c
        chosen = [id_map[i] for i in sorted(cited) if i in id_map]
        if chosen:
            return chosen
    return citations[:max_items]
