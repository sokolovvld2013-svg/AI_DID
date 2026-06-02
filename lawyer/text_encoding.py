"""Исправление кодировки имён файлов и текста (OCR, multipart, PDF)."""

import re
from urllib.parse import unquote

_URL_PATTERN = re.compile(
    r"https?://[^\s\]\)\"'<>]+|"
    r"www\.[^\s\]\)\"'<>]+|"
    r"localhost(?::\d+)?(?:/[^\s\]\)\"'<>]*)?",
    re.IGNORECASE,
)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")

# Латинские «двойники» кириллицы (частый сбой ToUnicode / OCR в PDF из Word).
_HOMOGLYPH_MAP: dict[str, str] = {
    "A": "А",
    "a": "а",
    "B": "В",
    "b": "ы",
    "C": "С",
    "c": "с",
    "D": "Д",
    "d": "д",
    "E": "Е",
    "e": "е",
    "F": "Ф",
    "f": "ф",
    "G": "Г",
    "g": "г",
    "H": "Н",
    "h": "н",
    "I": "И",
    "i": "и",
    "J": "Л",
    "j": "л",
    "K": "К",
    "k": "к",
    "L": "Л",
    "l": "л",
    "M": "М",
    "m": "м",
    "N": "Н",
    "n": "н",
    "O": "О",
    "o": "о",
    "P": "Р",
    "p": "р",
    "R": "Р",
    "r": "р",
    "S": "С",
    "s": "с",
    "T": "Т",
    "t": "т",
    "U": "У",
    "u": "у",
    "V": "В",
    "v": "в",
    "X": "Х",
    "x": "х",
    "Y": "У",
    "y": "у",
    "Z": "З",
    "z": "з",
}

_HOMOGLYPH_CHARS = frozenset(_HOMOGLYPH_MAP.keys())

# В «ломаных» словах латинские буквы читаются по-разному (E → С/У/Е, I → Л/И).
_AMBIGUOUS: dict[str, tuple[str, ...]] = {
    "E": ("Е", "С", "У"),
    "e": ("е", "с", "у"),
    "I": ("И", "Л"),
    "i": ("и", "л"),
    "H": ("Н", "Ж"),
    "h": ("н", "ж"),
}

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+", re.UNICODE)


def strip_urls(text: str) -> str:
    """Убрать ссылки на сайты из текста для отображения пользователю."""
    if not text:
        return text
    s = _MARKDOWN_LINK.sub(r"\1", text)
    s = _URL_PATTERN.sub("", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _cyrillic_score(s: str) -> int:
    return sum(1 for c in s if "\u0400" <= c <= "\u04FF" or c in "Ёё")


def _is_cyrillic_letter(c: str) -> bool:
    return ("\u0400" <= c <= "\u04FF") or c in "Ёё"


def _is_basic_latin_letter(c: str) -> bool:
    return c.isalpha() and ord(c) < 0x0300


def _homoglyph_latin_ratio(word: str) -> float:
    if not word:
        return 0.0
    letters = [c for c in word if c.isalpha()]
    if not letters:
        return 0.0
    homo = sum(1 for c in letters if c in _HOMOGLYPH_CHARS)
    return homo / len(letters)


def _looks_like_fake_cyrillic_word(word: str) -> bool:
    """Слово из латинских букв-двойников без нормальной кириллицы."""
    if not word:
        return False
    if _word_has_mixed_homoglyphs(word):
        return False
    letters = [c for c in word if c.isalpha()]
    if len(letters) < 2:
        return False
    if _cyrillic_score(word) >= max(2, len(word) // 3):
        return False
    ratio = _homoglyph_latin_ratio(word)
    if ratio < 0.65:
        return False
    if len(word) < 5 and ratio < 0.85:
        return False
    return True


def _word_has_mixed_homoglyphs(word: str) -> bool:
    """OCR/PDF: в одном слове и кириллица, и латинские «двойники» (СоМрОВОКИеННе)."""
    letters = [c for c in word if c.isalpha()]
    if len(letters) < 3:
        return False
    has_cyr = any("\u0400" <= c <= "\u04FF" or c in "Ёё" for c in letters)
    has_homo = any(c in _HOMOGLYPH_CHARS for c in letters)
    return has_cyr and has_homo


def _homoglyph_word_mixed(word: str) -> str:
    """Заменить латинские двойники в слове со смешанной раскладкой."""
    chars = list(word)
    out: list[str] = []
    for i, c in enumerate(chars):
        prev = out[-1] if out else None
        if c in _HOMOGLYPH_CHARS:
            out.append(_homoglyph_char(c, prev, i == len(chars) - 1, word_is_fake=True))
        else:
            out.append(c)
    return "".join(out)


def _homoglyph_char(c: str, prev: str | None, is_last: bool, *, word_is_fake: bool) -> str:
    if c in ("K", "k") and prev in ("у", "У", "y", "Y"):
        return "Ж" if c == "K" else "ж"
    if is_last and c in ("n", "N") and word_is_fake:
        return "й" if c == "n" else "Й"
    return _HOMOGLYPH_MAP.get(c, c)


def _homoglyph_word_simple(word: str) -> str:
    is_fake = _looks_like_fake_cyrillic_word(word)
    chars = list(word)
    out: list[str] = []
    for i, c in enumerate(chars):
        prev = out[-1] if out else None
        out.append(_homoglyph_char(c, prev, i == len(chars) - 1, word_is_fake=is_fake))
    return "".join(out)


def _homoglyph_word_disambiguated(word: str) -> str:
    """Подбор чтения для слов вроде EIEHNI → СЛУЖЕБ."""
    indices = [i for i, c in enumerate(word) if c in _AMBIGUOUS]
    if not indices or len(indices) > 6:
        return _homoglyph_word_simple(word)

    best = _homoglyph_word_simple(word)
    best_score = _cyrillic_score(best)

    def dfs(pos: int, chars: list[str]) -> None:
        nonlocal best, best_score
        if pos == len(indices):
            candidate = _homoglyph_word_simple("".join(chars))
            score = _cyrillic_score(candidate)
            if score > best_score:
                best_score = score
                best = candidate
            return
        i = indices[pos]
        c = chars[i]
        for v in _AMBIGUOUS[c]:
            chars[i] = v.lower() if c.islower() else v
            dfs(pos + 1, chars)
        chars[i] = c

    dfs(0, list(word))
    return best


_ISOLATED_HOMO = re.compile(
    r"(?<=[\s\d(,;:\-—])([A-Za-z])(?=[\s\d),.;:\-—]|$)"
)


def _fix_isolated_homoglyph_letters(text: str) -> str:
    """Одиночная латинская буква в русском тексте (B → В)."""

    def repl(m: re.Match[str]) -> str:
        c = m.group(1)
        return _HOMOGLYPH_MAP.get(c, c)

    return _ISOLATED_HOMO.sub(repl, text)


def _fix_homoglyph_latin(text: str) -> str:
    if not text:
        return text

    def replace_word(match: re.Match[str]) -> str:
        word = match.group(0)
        if _word_has_mixed_homoglyphs(word):
            return _homoglyph_word_mixed(word)
        if not _looks_like_fake_cyrillic_word(word):
            return word
        if any(c in _AMBIGUOUS for c in word):
            return _homoglyph_word_disambiguated(word)
        return _homoglyph_word_simple(word)

    text = _WORD_RE.sub(replace_word, text)
    return _fix_isolated_homoglyph_letters(text)


def _looks_like_ocr_case_chaos(word: str) -> bool:
    """Кириллица с хаотичным регистром после PDF/OCR (СоМрОВОКИеННе)."""
    letters = [c for c in word if c.isalpha()]
    if len(letters) < 6:
        return False
    if not all(_is_cyrillic_letter(c) for c in letters):
        return False
    upper = sum(1 for c in letters if c.isupper())
    lower = len(letters) - upper
    if upper == 0 or lower == 0:
        return False
    switches = sum(
        1
        for i in range(1, len(letters))
        if letters[i].isupper() != letters[i - 1].isupper()
    )
    return switches >= 3 and upper / len(letters) >= 0.22


def _fix_ocr_case_chaos(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        word = match.group(0)
        if _looks_like_ocr_case_chaos(word):
            return word.lower()
        return word

    return _WORD_RE.sub(repl, text)


def _replace_latin_in_cyrillic_text(text: str) -> str:
    """Латинские буквы в преимущественно кириллическом фрагменте → кириллица."""
    if _cyrillic_score(text) < 8:
        return text
    latin = sum(1 for c in text if _is_basic_latin_letter(c))
    if latin == 0:
        return text
    out: list[str] = []
    for c in text:
        if _is_basic_latin_letter(c) and c in _HOMOGLYPH_MAP:
            out.append(_HOMOGLYPH_MAP[c])
        else:
            out.append(c)
    return "".join(out)


def _ocr_garbage_score(text: str) -> int:
    score = 0
    for match in _WORD_RE.finditer(text):
        word = match.group(0)
        if _looks_like_ocr_case_chaos(word):
            score += 4
        if _word_has_mixed_homoglyphs(word):
            score += 3
        letters = [c for c in word if c.isalpha()]
        if len(letters) >= 8 and all(_is_cyrillic_letter(c) for c in letters):
            upper = sum(1 for c in letters if c.isupper())
            if upper >= 3 and 0.2 <= upper / len(letters) <= 0.85:
                score += 2
    score += sum(1 for c in text if _is_basic_latin_letter(c))
    return score


def _has_long_ocr_gibberish_words(text: str) -> bool:
    """Длинные «слова» без нормальной русской структуры после OCR/PDF."""
    long_words = 0
    for match in _WORD_RE.finditer(text):
        word = match.group(0)
        letters = [c for c in word if c.isalpha()]
        if len(letters) < 11:
            continue
        if not all(_is_cyrillic_letter(c) for c in letters):
            continue
        long_words += 1
        if long_words >= 2:
            return True
    return False


def citation_needs_llm_repair(text: str) -> bool:
    """Нужно ли вызывать LLM для читаемой цитаты."""
    if not text or len(text.strip()) < 20:
        return False
    if _ocr_garbage_score(text) >= 3:
        return True
    if _has_long_ocr_gibberish_words(text):
        return True
    return False


def repair_citation_text(text: str) -> str:
    """Агрессивное восстановление текста источников (без LLM)."""
    if not text:
        return text
    text = repair_text(text)
    text = _replace_latin_in_cyrillic_text(text)
    text = _fix_ocr_case_chaos(text)
    return _fix_homoglyph_latin(text)


def text_quality_score(text: str) -> tuple[int, int, int]:
    """Оценка качества извлечённого текста (для выбора варианта PDF)."""
    cleaned = repair_citation_text(text)
    return (
        _ru_plausibility(cleaned),
        _cyrillic_score(cleaned),
        -_ocr_garbage_score(cleaned),
    )


def _mojibake_penalty(s: str) -> int:
    """Штраф за типичный вид UTF-8→cp1251 (РќР° вместо На)."""
    low = s.lower()
    penalty = 0
    for marker in (
        "рќр°",
        "рѕс",
        "сѓс",
        "рїр",
        "рІр",
        "рјр",
        "рµр",
        "р»р",
        "рґр",
        "рір",
        "р°р",
        "рёр",
        "рёр",
        "рѕр",
        "рёр",
        "рёр",
    ):
        penalty += low.count(marker) * 8
    penalty += len(re.findall(r"Р[а-яА-ЯёЁ]{1,2}(?=Р)", s)) * 3
    return penalty


def _ru_plausibility(s: str) -> int:
    """Чем выше — тем больше похоже на нормальный русский текст."""
    if not s:
        return 0
    low = s.lower()
    score = _cyrillic_score(s) * 2
    for frag in (
        " на ",
        " не ",
        " по ",
        " что ",
        " при ",
        " для ",
        " это ",
        " отсутств",
        " предост",
        " информа",
        " текст",
        " упомин",
        " основ",
        " матери",
        " помощ",
        " фрагм",
        " вопрос",
        " докум",
        " стать",
        " срок",
    ):
        if frag in low:
            score += 20
    score -= _mojibake_penalty(s)
    return score


def _try_utf8_mojibake_fix(text: str) -> str | None:
    """UTF-8, ошибочно показанный как cp1251/latin-1: РќР° → На."""
    if not text:
        return None

    original_score = _ru_plausibility(text)
    best: str | None = None
    best_score = original_score

    for enc in ("cp1251", "latin-1", "cp1252"):
        try:
            candidate = text.encode(enc).decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
        if candidate == text:
            continue
        score = _ru_plausibility(candidate)
        if score > best_score:
            best_score = score
            best = candidate

    if best is not None and best_score > original_score + 5:
        return best
    return None


def _encoding_variants(text: str) -> list[str]:
    candidates = [text]
    for enc in ("latin-1", "cp1251", "cp1252"):
        try:
            candidates.append(text.encode(enc).decode("utf-8"))
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    try:
        candidates.append(text.encode("utf-8").decode("cp1251"))
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return candidates


def repair_text(text: str) -> str:
    """Восстановление читаемого русского текста (кодировка + латинские двойники)."""
    if not text:
        return text

    mojibake_fixed = _try_utf8_mojibake_fix(text)
    if mojibake_fixed:
        text = mojibake_fixed

    original = text
    candidates: list[str] = []

    for variant in _encoding_variants(text):
        candidates.append(variant)
        candidates.append(_fix_homoglyph_latin(variant))

    candidates.append(_fix_homoglyph_latin(text))

    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    def _mixed_word_penalty(s: str) -> int:
        return sum(15 for m in _WORD_RE.finditer(s) if _word_has_mixed_homoglyphs(m.group(0)))

    def _quality(s: str) -> tuple[int, int, int]:
        return (
            _ru_plausibility(s),
            _cyrillic_score(s),
            -sum(1 for c in s if c in _HOMOGLYPH_CHARS)
            - _mixed_word_penalty(s),
        )

    best = max(unique, key=_quality)
    if _quality(best) > _quality(original):
        result = best
    else:
        result = original
    return _fix_homoglyph_latin(result)


def decode_upload_filename(name: str | None) -> str:
    if not name:
        return "document"
    name = name.strip()
    if "%" in name:
        try:
            name = unquote(name, encoding="utf-8")
        except Exception:
            pass
    return repair_text(name)
