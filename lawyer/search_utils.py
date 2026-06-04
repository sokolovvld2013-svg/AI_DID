"""Поиск по базе юриста: гибрид эмбеддингов и ключевых слов."""

import re
from difflib import SequenceMatcher

# Не считаем «ядром» запроса — иначе отсекаются нормальные фрагменты
_STOP_WORDS = frozenset(
    """
    и в во не на что как по для при это из кто который от до все также или
    ли бы же уже при том что бы там тут где когда если то есть при
    """.split()
)

_TOKEN_ALIASES: dict[str, list[str]] = {
    "бизне": ["бизнес", "бизнеса", "бизнесу", "бизнес-план", "бизнес план"],
    "безнес": ["бизнес", "бизнес-план"],
    "планн": ["план"],
    "приказ": ["приказа", "приказе"],
    "положен": ["положение", "положения", "положении"],
    "закуп": ["закупка", "закупки", "закупок", "закупке", "закупках", "закупочная"],
    "закупк": ["закупка", "закупки", "закупок"],
    "договор": ["договора", "договору", "договором", "договоры"],
    "поставщик": ["поставщика", "поставщиком", "поставщики"],
    "контракт": ["контракта", "контракту", "контракты"],
    "стать": ["статья", "статьи", "статье", "статью"],
    "пункт": ["пункта", "пункте", "пункты"],
    "срок": ["срока", "сроки", "сроков"],
    "ответствен": ["ответственность", "ответственности"],
}

# Длина общего префикса для «закупок» / «закупка» и т.п.
_RU_PREFIX_MIN = 5

_RE_TOKEN = re.compile(r"[\wа-яё]+", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    return _RE_TOKEN.findall(text.lower())


def normalize_match_text(text: str) -> str:
    """Текст для нечёткого поиска: без лишних пробелов и переносов."""
    s = text.lower().replace("\u00ad", "").replace("‐", "-")
    s = re.sub(r"[\s\-–—]+", " ", s)
    return s.strip()


def core_query_tokens(query: str) -> list[str]:
    """Значимые слова запроса (без стоп-слов)."""
    tokens = [t for t in tokenize(query) if len(t) >= 2 and t not in _STOP_WORDS]
    # Длинные слова важнее; короткие (2 буквы) — только если других нет
    long_t = [t for t in tokens if len(t) >= 3]
    return long_t if long_t else tokens


def expand_query_tokens(query: str) -> list[str]:
    tokens = tokenize(query)
    expanded: set[str] = set()
    for t in tokens:
        if len(t) < 2:
            continue
        expanded.add(t)
        for alias in _TOKEN_ALIASES.get(t, []):
            expanded.add(alias)
            for part in tokenize(alias):
                expanded.add(part)
        if len(t) >= 4:
            expanded.add(t[:4])

    if len(tokens) >= 2:
        expanded.add("-".join(tokens))
        expanded.add(" ".join(tokens))
        expanded.add("".join(tokens))

    return sorted(expanded, key=len, reverse=True)


def expand_query_phrases(query: str) -> list[str]:
    q = query.strip()
    phrases = [q, enrich_query_for_embedding(query)]
    low = normalize_match_text(q)
    words = [w for w in low.split() if len(w) >= 2 and w not in _STOP_WORDS]
    core = core_query_tokens(query)
    variants = [
        low,
        low.replace("бизне", "бизнес"),
        re.sub(r"\s+", " ", low),
        low.replace(" ", "-"),
        low.replace(" ", ""),
        " ".join(core),
        "-".join(core),
    ]
    for i in range(len(words) - 1):
        variants.append(f"{words[i]} {words[i + 1]}")
    if len(words) >= 3:
        for i in range(len(words) - 2):
            variants.append(f"{words[i]} {words[i + 1]} {words[i + 2]}")
    for t in core:
        if len(t) >= 4:
            variants.append(t)
    seen: set[str] = set()
    for p in variants:
        if p and p not in seen:
            seen.add(p)
            phrases.append(p)
    return phrases


def enrich_query_for_embedding(query: str) -> str:
    """Усиленный текст запроса для эмбеддинга (ключевые слова дважды)."""
    q = query.strip()
    core = core_query_tokens(query)
    if not core:
        return q
    extra = " ".join(core)
    return f"{q}. Ключевые термины: {extra}. {extra}"


def _shared_prefix_len(a: str, b: str, min_len: int = _RU_PREFIX_MIN) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i if i >= min_len else 0


def _token_in_document(token: str, doc_lower: str, doc_tokens: list[str]) -> bool:
    compact = doc_lower.replace(" ", "").replace("-", "")
    token_compact = token.replace("-", "")
    if token in doc_lower:
        return True
    if len(token) >= 3 and token_compact in compact:
        return True
    if len(token) >= 4 and token[:4] in doc_lower:
        return True
    if len(token) >= 3 and token[:3] in compact:
        return True
    prefix = token[:_RU_PREFIX_MIN] if len(token) >= _RU_PREFIX_MIN else ""
    if prefix and prefix in doc_lower:
        return True
    for dt in doc_tokens:
        if token == dt:
            return True
        if prefix and _shared_prefix_len(token, dt) >= _RU_PREFIX_MIN:
            return True
        if len(token) >= 4 and len(dt) >= 4 and token[:4] == dt[:4]:
            return True
        if len(token) >= 3 and len(dt) >= 3:
            if SequenceMatcher(None, token, dt).ratio() >= 0.78:
                return True
    return False


def count_core_matches(core: list[str], document: str) -> int:
    if not core or not document:
        return 0
    doc_lower = normalize_match_text(document)
    doc_tokens = tokenize(document)
    matched = 0
    for t in core:
        if _token_in_document(t, doc_lower, doc_tokens):
            matched += 1
            continue
        # OCR: «бизнес» как «бизн» / разрыв «бизнес план» → «бизнес-план»
        if len(t) >= 4 and t[:4] in doc_lower.replace("-", "").replace(" ", ""):
            matched += 1
    return matched


def core_words_in_order(core: list[str], document: str) -> bool:
    """Ключевые слова встречаются в документе в том же порядке (между ними могут быть другие слова)."""
    if not core:
        return False
    doc = normalize_match_text(document)
    pos = 0
    for word in core:
        idx = doc.find(word, pos)
        if idx < 0:
            return False
        pos = idx + len(word)
    return True


def query_search_substrings(query: str, core: list[str]) -> list[str]:
    """Подстроки для поиска в Chroma и в тексте (включая «для» и окна из запроса)."""
    seen: set[str] = set()
    out: list[str] = []

    def add(s: str) -> None:
        s = normalize_match_text(s)
        if len(s) >= 8 and s not in seen:
            seen.add(s)
            out.append(s)

    add(query)
    tokens = tokenize(query)
    for i in range(len(tokens)):
        for j in range(i + 2, min(i + 10, len(tokens) + 1)):
            add(" ".join(tokens[i:j]))

    if len(core) >= 2:
        add(" ".join(core))
        add(" ".join(core[-2:]))
        add(" ".join(core[-3:]))
    if len(core) >= 4:
        add(f"{core[0]} {core[1]} для {core[2]} {core[3]}")
        add(f"{core[1]} для {' '.join(core[2:])}")

    return sorted(out, key=len, reverse=True)


def query_phrase_score(query: str, core: list[str], document: str) -> float:
    """Бонус за точную или порядковую фразу (важно для «…для служебного пользования»)."""
    if not document:
        return 0.0
    doc = normalize_match_text(document)
    score = 0.0

    for sub in query_search_substrings(query, core):
        if sub in doc:
            score = max(score, 50.0 + min(len(sub), 40))

    if len(core) >= 2:
        if core_words_in_order(core, doc):
            score = max(score, 45.0)

    return score


def query_phrase_score_with_context(
    query: str,
    core: list[str],
    chunk_text: str,
    context_texts: list[str] | None = None,
) -> float:
    """Фраза может быть разорвана границей чанка — проверяем склейку с соседями."""
    score = query_phrase_score(query, core, chunk_text)
    if context_texts:
        merged = normalize_match_text(
            " ".join([chunk_text] + [t for t in context_texts if t])
        )
        score = max(score, query_phrase_score(query, core, merged))
    return score


def keyword_score_core(core: list[str], document: str) -> float:
    """Совпадение только по значимым словам запроса (без раздувания expand_query_tokens)."""
    if not core:
        return 0.0
    return keyword_score(core, document)


def keyword_score(query_tokens: list[str], document: str) -> float:
    if not query_tokens or not document:
        return 0.0

    doc_lower = normalize_match_text(document)
    doc_tokens = tokenize(document)
    score = 0.0
    core = [t for t in query_tokens if len(t) >= 3 and t not in _STOP_WORDS]

    for qt in query_tokens:
        if len(qt) < 2:
            continue
        if _token_in_document(qt, doc_lower, doc_tokens):
            score += 5.0 if qt in core else 3.0
            continue
        best = 0.0
        for dt in doc_tokens:
            if len(qt) >= 3 and len(dt) >= 3:
                ratio = SequenceMatcher(None, qt, dt).ratio()
                if ratio >= 0.78:
                    best = max(best, 3.0 * ratio)
        score += best

    # Фраза из нескольких слов подряд в документе
    if len(core) >= 2:
        phrase = " ".join(core[:5])
        if phrase in doc_lower:
            score += 8.0
        compact_phrase = phrase.replace(" ", "")
        if compact_phrase in doc_lower.replace(" ", ""):
            score += 6.0

    return score


def phrase_bonus(core: list[str], document: str) -> float:
    """Бонус, если в фрагменте есть все ключевые слова запроса."""
    if len(core) < 2:
        return 0.0
    n = count_core_matches(core, document)
    if n == len(core):
        return 12.0
    if n >= len(core) - 1 and n >= 2:
        return 5.0
    if n >= 1:
        return 1.5
    return 0.0


def combined_score(
    semantic: float,
    keyword: float,
    core_match_ratio: float,
    phrase_score: float = 0.0,
) -> float:
    phrase_boost = min(phrase_score / 80.0, 1.0) * 0.5
    kw_norm = min(keyword / 18.0, 1.0)
    core_r = min(core_match_ratio, 1.0)
    if phrase_boost >= 0.35:
        return min(1.0, phrase_boost + 0.15 * kw_norm + 0.1 * semantic + core_r * 0.15)
    # Полное совпадение терминов запроса — главный сигнал для регламентов и положений
    if core_r >= 1.0:
        return min(1.0, 0.1 * semantic + 0.35 * kw_norm + 0.55 + phrase_boost)
    if core_r >= 0.66:
        return min(1.0, 0.15 * semantic + 0.45 * kw_norm + core_r * 0.4)
    if kw_norm >= 0.2 or core_r >= 0.5:
        return min(1.0, 0.2 * semantic + 0.55 * kw_norm + core_r * 0.3)
    return min(1.0, 0.5 * semantic + 0.25 * kw_norm + core_r * 0.2)


def reciprocal_rank_fusion(rank_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    """RRF: объединение ранжирований (семантика + ключевые слова)."""
    scores: dict[str, float] = {}
    for ranked in rank_lists:
        for i, key in enumerate(ranked):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + i + 1)
    return scores


def min_core_matches_required(core: list[str]) -> int:
    """Сколько слов запроса должно совпасть (не требуем 100% для длинных вопросов)."""
    if not core:
        return 0
    if len(core) == 1:
        return 1
    if len(core) == 2:
        return 2
    if len(core) == 3:
        return 2
    return max(2, (len(core) * 2 + 2) // 3)
