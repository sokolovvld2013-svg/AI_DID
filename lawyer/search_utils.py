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
}

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
    phrases = [q]
    low = normalize_match_text(q)
    words = [w for w in low.split() if len(w) >= 2 and w not in _STOP_WORDS]
    variants = [
        low,
        low.replace("бизне", "бизнес"),
        re.sub(r"\s+", " ", low),
        low.replace(" ", "-"),
        low.replace(" ", ""),
    ]
    for i in range(len(words) - 1):
        variants.append(f"{words[i]} {words[i + 1]}")
    if len(words) >= 3:
        for i in range(len(words) - 2):
            variants.append(f"{words[i]} {words[i + 1]} {words[i + 2]}")
    seen: set[str] = set()
    for p in variants:
        if p and p not in seen:
            seen.add(p)
            phrases.append(p)
    return phrases


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
    for dt in doc_tokens:
        if token == dt:
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
        return 6.0
    if n >= len(core) - 1 and n >= 1:
        return 2.0
    return 0.0


def combined_score(semantic: float, keyword: float, core_match_ratio: float) -> float:
    kw_norm = min(keyword / 12.0, 1.0)
    core_boost = min(core_match_ratio, 1.0) * 0.3
    # Явное совпадение слов важнее «похожести» эмбеддинга на искажённом OCR-тексте
    if kw_norm >= 0.25 or core_match_ratio >= 0.5:
        return min(1.0, 0.2 * semantic + 0.6 * kw_norm + core_boost)
    return min(1.0, 0.55 * semantic + 0.3 * kw_norm + core_boost)


def min_core_matches_required(core: list[str]) -> int:
    """Сколько слов запроса должно совпасть (не требуем 100% для длинных вопросов)."""
    if not core:
        return 0
    if len(core) == 1:
        return 1
    if len(core) == 2:
        return 1
    return max(1, (len(core) + 1) // 2)
