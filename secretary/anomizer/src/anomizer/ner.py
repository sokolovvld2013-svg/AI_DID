from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Set
from pymorphy2 import MorphAnalyzer
from natasha import (
    Segmenter,
    MorphVocab,
    NewsEmbedding,
    NewsNERTagger,
    Doc,
)

from .registry import Entity, RegexPatterns


# Организационно-правовые формы и прочие служебные слова в наименовании юрлица.
# Вырезаются при построении канонического ключа.
_ORG_FORM_WORDS = re.compile(
    r'\b(?:'
    r'обществ[ао]|открытое|закрытое|акционерное|публичное|непубличное|'
    r'государственное|муниципальное|унитарное|единое|'
    r'ответственностью|предприяти[ея]|'
    r'ооо|оао|зао|пао|ао|аон|гуп|муп|фгуп|фгбу|фгкц|ано|нко|дпо|оод|'
    r'нк|тсж|снт|дмп|учреждение|организация'
    r')\b',
    re.IGNORECASE,
)


def phone_canon(text: str) -> str:
    """Канонический ключ телефона: только значащие цифры номера.

    «+7 (495) 145-70-07», «8-495-145-70-07», «(495) 145-70-07» и
    «(495) 145-70-07 доб. 12» дают ключ «4951457007» — один плейсхолдер
    на один номер. Код страны/оператора (7 или 8) и добавочный отбрасываются.
    """
    digits = re.sub(r'\D', '', re.split(r'доб', text, maxsplit=1, flags=re.IGNORECASE)[0])
    if len(digits) == 11 and digits[0] in '78':
        digits = digits[1:]
    return digits


def org_canon(name: str) -> str:
    """Канонический ключ юрлица: разные написания → один плейсхолдер.

    «Общество с ограниченной ответственностью «Ромашка-Авто»»,
    «ООО «Ромашка-Авто»» и «Ромашка-Авто (44717890)» дают один ключ
    «ромашка-авто», поэтому обезличиваются одинаково. Регистр, «ё»,
    кавычки, скобки и организационно-правовая форма не влияют.
    """
    s = name.replace('ё', 'е')
    s = _ORG_FORM_WORDS.sub(' ', s)
    s = re.sub(r'[^0-9A-Za-zА-Яа-яЕе\s-]', ' ', s)
    s = re.sub(r'[\s_-]+', ' ', s).strip().lower()
    if s:
        return s
    # Название состояло только из ОПФ — не даём всем таким организациям
    # один и тот же пустой ключ.
    fallback = re.sub(r'[\s_-]+', ' ', re.sub(r'[^0-9A-Za-zА-Яа-яЕе\s-]', ' ', s)).strip()
    return fallback or name.strip().lower()


DID_ORG_CANON = org_canon('ФГУП «Дирекция по инвестиционной деятельности»')

_ROSUIM_CANON = org_canon('Федеральное агентство по управлению государственным имуществом (Росимущество)')

_QUOTE_CHARS = '[«»"“”]'

# Регуляторные и организационные аббревиатуры: pymorphy2 разбирает их как
# имя/отчество («ИНН» → Name), но персоной они не являются.
_PERSON_STOPWORDS = {
    'инн', 'огрн', 'октмо', 'окпо', 'кпп', 'снилс', 'бик', 'оквед', 'уин',
    'кбк', 'ооо', 'оао', 'зао', 'пао', 'ао', 'аон', 'гуп', 'муп', 'фгуп',
    'фгбу', 'ано', 'нко', 'дпо', 'оод', 'тсж', 'снт', 'дмп', 'мп', 'ио',
    'учредитель', 'заявитель', 'покупатель', 'арендатор', 'арендодатель',
    'подрядчик', 'заказчик', 'исполнитель', 'продавец', 'контрагент',
}

# Знаки, снимаемые с края слова перед разбором (склейку делать нельзя —
# см. _is_person_word).
_PERSON_STRIP = " \t\n\r,.;:!?()«»\"“”„'‑"

# Маркеры подписи/печати, которые НЕ входят в персону и отрезаются с конца спана
# («Е.В. Иночкин М.П.» → «Е.В. Иночкин»). Нормализованные по буквам.
_PERSON_TAIL_MARKERS = {'мп'}

# Слово или инициалы внутри PERSON-спана: «Иночкин», «И.О.», «М.П.», «Иванов-Ильин»
# разбирается на «Иванов» + «Ильин».
_WORD_TOKEN = re.compile(r'[А-ЯЁа-яёA-Za-z]+[.]*')

# Разделители, снимаемые с края PERSON-спана после отсечения хвоста.
# Точка намеренно не входит: она часть инициалов («Иванов И.О.»).
_PERSON_TAIL_STRIP = set(" \t\n\r,;:")

# Автодетект контрагента: «…, именуемое в дальнейшем «Заказчик»» — так в договорах
# объявляется вторая сторона. group(1) — наименование организации (и её сокращение в скобках).
_ORG_DECL_PATTERN = re.compile(
    r'(?<![А-ЯЁа-яё])'
    r'([А-ЯЁ«][^;,\n]{2,}?)'
    r'\s*,?\s+именуем\w*\s+в\s+дальнейшем\s+'
    r'[«"„“][^»""]*?[»"”]'
)

# Объявление контрагента не может начинаться с реквизита или служебного слова:
# «ИНН 7700000000, именуемое в дальнейшем…» — это не наименование.
_ORG_DECL_REJECT = re.compile(
    r'^\W*(?:инн|огрн|октмо|окпо|кпп|снилс|бик|оквед|уин|кбк|'
    r'заказчик|арендодатель|арендатор|исполнитель|подрядчик|покупатель|'
    r'продавец|заявитель|сторона|контрагент)\b',
    re.IGNORECASE,
)

# Название в кавычках: «Ромашка-Авто», "Ромашка-Авто".
# Нужна хотя бы одна буква: бланк «_____» в заголовке договора названием не является.
_QUOTED_NAME = re.compile(r'[«"„“][^«»"“”]*[А-Яа-яЁё][^«»"“”]*[»"”]')

# Сокращение организации в скобках сразу после полного наименования:
# АО «Сбербанк- Автоматизированная система торгов» («Сбербанк- АСТ»)
_PAREN_ALIAS_PATTERN = re.compile(
    r'\s*\(\s*([«"„“][^«»"“”]*[А-Яа-яЁё][^«»"“”]*[»"”])\s*\)'
)

_LEGAL_QUOTE_OPEN = '[«"“„]'
_LEGAL_QUOTE_CLOSE = '[»"”]'
_LEGAL_QUOTE_INNER = '[^«»"“”„\n]'

# Полная организационно-правовая форма + наименование в кавычках (с флексиями).
# «Общество с ограниченной ответственностью «ДЕЗ»», «Акционерное общество «...»» и т.п.
_LEGAL_ENTITY_PATTERN = re.compile(
    r'(?:'
    r'общество[а-яё]*\s+с\s+ограниченн[а-яё]*\s+ответственност[а-яё]*'
    r'|(?:открыт[а-яё]*|закрыт[а-яё]*|публичн[а-яё]*|непубличн[а-яё]*)?\s*'
    r'акционерн[а-яё]*\s+общество[а-яё]*'
    r'|(?:федеральн[а-яё]*\s+)?государственн[а-яё]*\s+унитарн[а-яё]*\s+предприяти[а-яё]*'
    r'|муниципальн[а-яё]*\s+унитарн[а-яё]*\s+предприяти[а-яё]*'
    r')\s*'
    r'(' + _LEGAL_QUOTE_OPEN + r')(' + _LEGAL_QUOTE_INNER + r'{2,60}?)(' + _LEGAL_QUOTE_CLOSE + r')',
    re.IGNORECASE
)

# Сокращённые организационно-правовые формы + наименование в кавычках:
# «ООО «ДЕЗ»», «АО "Ромашка"», «ФГУП «...»» (ДИД покрывается отдельным паттерном).
_LEGAL_ABBREV = (
    r'(?:ООО|ОАО|ЗАО|ПАО|АО|ГУП|МУП|ФГУП|ФГБУ|АНО|НКО|ОДО|НП|'
    r'ТСЖ|СНТ|ДНП|ЖСК)'
)
_LEGAL_ABBREV_PATTERN = re.compile(
    r'(?<![А-ЯЁа-яё])' + _LEGAL_ABBREV + r'(?![А-ЯЁа-яё])'
    r'\s*(' + _LEGAL_QUOTE_OPEN + r')(' + _LEGAL_QUOTE_INNER + r'{2,60}?)(' + _LEGAL_QUOTE_CLOSE + r')',
    re.IGNORECASE
)

# Название ДИД: между словами допускается перенос строки (в DOCX длинное
# наименование часто разбито), после ФГУП — кавычка или пробел.
# Скобки намеренно не захватываются, чтобы не ломать парность «(ФГУП «ДИД»)».
_DID_NAME = r'дирекция\s+по\s+инвестиционной\s+деятельности'
_DID_GAP = r'[\s«"„“]*'
_DID_TAIL = r'[»"”]*'

# Базовый список организаций (ФГУП «ДИД» во всех написаниях + Росимущество отдельно).
_ORG_PATTERN_PARTS = [
    # Полное наименование с любой флексией: «Федеральн* государственн* унитарн* предприяти*…»
    r'федеральн[а-яё]*\s+государственн[а-яё]*\s+унитарн[а-яё]*\s+предприяти[а-яё]*'
    + _DID_GAP + r'(?:' + _DID_NAME + r'|дид)' + _DID_TAIL,
    # Сокращённо: «ФГУП «ДИД»» / «ФГУП «Дирекция…»» / «ФГУП (ДИД)»
    r'фгуп' + _DID_GAP + r'(?:' + _DID_NAME + r'|дид)' + _DID_TAIL,
    # Название в кавычках без ФГУП
    r'(?:' + _QUOTE_CHARS + r')' + _DID_NAME + r'(?:' + _QUOTE_CHARS + r')?',
    # Короткое имя
    r'\bдид\b',
]

# Настройки приложения читаются лениво: пакет можно использовать и без FastAPI.
_ORG_FORM_PREFIX = re.compile(
    r'^(?:общество\s+с\s+ограниченн[а-яё]*\s+ответственност[а-яё]*|'
    r'(?:федеральн[а-яё]*|государственн[а-яё]*|муниципальн[а-яё]*|частн[а-яё]*|'
    r'открыт[а-яё]*|закрыт[а-яё]*|публичн[а-яё]*|непубличн[а-яё]*|акционерн[а-яё]*)\s+)?'
    r'(?:общество|унитарн[а-яё]*\s+предприяти[а-яё]*|предприяти[а-яё]*)?\s*',
    re.IGNORECASE,
)
_COMPANY_ORG_CACHE: Dict[str, Any] = {}


def _current_company_name() -> str:
    """Наименование компании из настроек приложения (пусто, если недоступно)."""
    try:
        from core.settings import get_company_name

        return (get_company_name() or '').strip()
    except Exception:
        return ''


def _company_pattern_parts(company_name: str) -> List[str]:
    """Варианты написания наименования компании для регулярного выражения."""
    if not company_name:
        return []
    variants = {company_name}
    quoted = re.search(r'([«"„“])([^«»"“”]{2,})[»"”]', company_name)
    if quoted:
        variants.add(quoted.group(2))
    bare = _ORG_FORM_PREFIX.sub('', company_name).strip(' \t«»""„“')
    if len(bare) >= 3:
        variants.add(bare)
    parts = []
    for variant in variants:
        words = [re.escape(word) for word in re.split(r'\s+', variant.strip()) if word]
        if words:
            parts.append(r'[\s]+'.join(words))
    return sorted(set(parts), key=len, reverse=True)


def company_org_pattern():
    """Паттерн организаций: базовый + текущее наименование компании из настроек.

    Кэш по названию, поэтому переименование компании подхватывается сразу.
    """
    company_name = _current_company_name()
    cached = _COMPANY_ORG_CACHE.get(company_name)
    if cached is None:
        parts = [p for p in [*_company_pattern_parts(company_name), *_ORG_PATTERN_PARTS] if p]
        cached = re.compile(r'(?:' + '|'.join(parts) + r')', re.IGNORECASE)
        _COMPANY_ORG_CACHE.clear()
        _COMPANY_ORG_CACHE[company_name] = cached
    return cached


DID_ORG_PATTERN = company_org_pattern()

# Росимущество: слово в любом склонении/регистре и полное наименование в любых падежах
# («Федерального агентства по управлению государственным имуществом» и т.п.)
ROSUIM_PATTERN = re.compile(
    r'(?:'
    r'(?:федеральн[а-яё]*\s+)?агентств[а-яё]*\s+по\s+управлению\s+государственн[а-яё]*\s+имуществ[а-яё]*'
    r'|'
    r'\bросимуществ[а-яё]*'
    r')',
    re.IGNORECASE
)


def parse_orgs_string(raw: str) -> List[str]:
    """Разобрать строку организаций (через запятую, точку с запятой или перевод строки)."""
    if not raw or not raw.strip():
        return []
    parts = re.split(r'[,;\n]+', raw)
    return [p.strip() for p in parts if p.strip()]


@dataclass
class ExtractedEntity:
    text: str
    normalized: str
    entity_type: str
    start: int
    end: int


class MorphNormalizer:
    # Знаки, которыми обрезаются сущности по краям (не входят в сами сущности)
    STRIP_CHARS = set(" \t\n\r,;.:!?()")

    def __init__(self):
        self.morph = MorphAnalyzer()
        self._cache: dict[str, str] = {}

    def normalize(self, text: str, entity_type: str = None) -> str:
        cache_key = f"{entity_type}:{text}" if entity_type else text
        if cache_key in self._cache:
            return self._cache[cache_key]

        if entity_type == "PERSON":
            result = self._person_signature(text)
        else:
            result = self._word_forms(text)

        self._cache[cache_key] = result
        return result

    def _word_forms(self, text: str) -> str:
        """Нормализация по начальным формам слов (для всех типов кроме PERSON)."""
        normalized_words = []
        for word in text.split():
            parsed = self.morph.parse(word)
            if parsed:
                normalized_words.append(parsed[0].normal_form)
            else:
                normalized_words.append(word.lower())
        return ' '.join(normalized_words)

    def _person_signature(self, text: str) -> str:
        """Подпись лица: фамилия + инициалы имени и отчества (без учёта падежа и порядка слов).

        «Иночкина Евгения Валериевича» и «Е.В. Иночкин» дают одинаковую подпись.
        """
        surname = None
        initials: list[str] = []

        for word in text.split():
            letters = self._extract_initial_letters(word)
            if letters:
                initials.extend(letters)
                continue

            parsed = self.morph.parse(word)
            if not parsed:
                continue

            person_variants = [
                p for p in parsed
                if any(t in str(p.tag) for t in ("Surn", "Name", "Patr"))
            ]
            if not person_variants:
                continue

            p = person_variants[0]
            tag = str(p.tag)
            if "Surn" in tag and surname is None:
                surname = p.normal_form
            elif "Name" in tag or "Patr" in tag:
                if p.normal_form:
                    initials.append(p.normal_form[0])

        if surname is None:
            # Запасной вариант: если фамилия не распознана — по обычным формам
            return self._word_forms(text)

        # Сортировка и уникальность инициалов: «Е.В. Иночкин» == «Иночкин Евгений Валерьевич»
        initials = sorted(set(initials))
        return f"{surname}|{' '.join(initials)}"

    @staticmethod
    def _extract_initial_letters(word: str) -> list[str]:
        """Ищет инициалы вида Е.В. / Е. / Е.В — возвращает буквы."""
        letters = re.findall(r'[А-ЯЁ](?=\.)', word)
        return [ch.lower() for ch in letters]

    def trim_span(self, text: str, start: int, end: int, entity_type: str) -> tuple[int, int]:
        """Убирает разделители с границ спана; для ORG — обрезает по первой запятой/точке с запятой.

        Нужно против того, что Natasha печатает огромные ORG-списки текста до запятой,
        «съедая» фразы из-за одного плейсхолдера.
        """
        s, e = start, end
        while s < e and text[s] in self.STRIP_CHARS:
            s += 1
        while e > s and text[e - 1] in self.STRIP_CHARS:
            e -= 1

        if entity_type == "ORG":
            for sep in (",", ";"):
                idx = text.find(sep, s, e)
                if idx != -1:
                    e = idx
                    while e > s and text[e - 1] in self.STRIP_CHARS:
                        e -= 1
                    break

        return s, e


class NERExtractor:
    PERSON_TYPE = "PERSON"
    ORG_TYPE = "ORG"

    # Минимум букв в слове-«имени»: одиночные буквы pymorphy2 помечает
    # как имя/отчество, хотя это инициалы или служебные слова.
    MIN_PERSON_WORD_LEN = 2
    
    def __init__(self):
        self.segmenter = Segmenter()
        self.morph_vocab = MorphVocab()
        self.emb = NewsEmbedding()
        self.ner_tagger = NewsNERTagger(self.emb)
        self.morph_normalizer = MorphNormalizer()
        self.morph = MorphAnalyzer()
        
        self._regex_patterns = {
            'EMAIL': RegexPatterns.EMAIL,
            'PHONE': RegexPatterns.PHONE,
            'INN': RegexPatterns.INN,
        }
        
        self._initials_pattern = re.compile(
            r'\b(?:[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*[А-ЯЁ][а-яё]+'
            r'|[А-ЯЁ][а-яё]+\s+(?:[А-ЯЁ]\.)+)'
            r'(?=[^А-ЯЁа-яёA-Za-z0-9]|$)'
        )

    def _trim_person_tail(self, text: str, start: int, end: int) -> Tuple[int, int]:
        """Отрезает с конца персоны маркеры «М.П.» и обычные слова после ФИО.

        Natasha тянет в PER-спан и место печати («М.П.»), и слово после имени
        («Е.В. Иночкин\\nКонтакт»), из-за чего плейсхолдер «съедал» лишнее.
        """
        start, end = self._trim_tail_markers(text, start, end)
        return self._trim_tail_common_words(text, start, end)

    @staticmethod
    def _trim_tail_markers(text: str, start: int, end: int) -> Tuple[int, int]:
        """Отрезает с конца спана маркеры типа «М.П.» (место печати)."""
        s, e = start, end
        while True:
            m = re.search(r'\s*(\S+)\s*$', text[s:e])
            if not m:
                break
            letters = re.sub(r'[^А-Яа-яё]', '', m.group(1)).lower()
            if letters in _PERSON_TAIL_MARKERS:
                e = s + m.start()
            else:
                break
        return s, e

    def _trim_tail_common_words(self, text: str, start: int, end: int) -> Tuple[int, int]:
        """Убирает нарицательные слова в конце PERSON-спана.

        «Е.В. Иночкин Контакт» → «Е.В. Иночкин». Слово считается частью ФИО,
        если pymorphy2 находит в нём тег Surn/Name/Patr; одиночные буквы
        («И.О.», «М.П.») остаются — это инициалы. Если после обрезки не
        остаётся ни одного слова-имени, спан не трогаем.
        """
        span = text[start:end]
        tokens = list(_WORD_TOKEN.finditer(span))
        if not tokens:
            return start, end

        cut: Optional[int] = None
        for m in reversed(tokens):
            core = m.group().rstrip('.')
            if self._is_person_word(core):
                break
            if len(core) >= self.MIN_PERSON_WORD_LEN:
                # нарицательное в хвосте — кандидат на отсечение
                cut = m.start() if cut is None else cut
        else:
            # ни одного слова-имени в спане: резать нечего
            return start, end

        if cut is None:
            return start, end

        e = start + cut
        # Снять разделитель, оставшийся между ФИО и отсечённым словом.
        # Точку не трогаем — она часть инициалов («Иванов И.О.»).
        while e > start and text[e - 1] in _PERSON_TAIL_STRIP:
            e -= 1
        return start, e


    def extract(self, text: str, extra_orgs: Optional[List[str]] = None) -> List[ExtractedEntity]:
        entities = []
        
        entities.extend(self._extract_natasha(text))
        entities.extend(self._extract_russian_names(text))
        entities.extend(self._extract_regex(text))
        entities.extend(self._extract_known_orgs(text, extra_orgs))
        entities.extend(self._extract_counterparty_orgs(text))
        entities.extend(self._extract_legal_entities(text, entities))
        
        entities = self._merge_overlapping(entities)
        entities.sort(key=lambda e: e.start)
        
        return entities

    def _extract_natasha(self, text: str) -> List[ExtractedEntity]:
        doc = Doc(text)
        doc.segment(self.segmenter)
        doc.tag_ner(self.ner_tagger)
        
        entities = []
        for span in doc.spans:
            if span.type != 'PER':
                continue
            s, e = self.morph_normalizer.trim_span(text, span.start, span.stop, self.PERSON_TYPE)
            s, e = self._trim_person_tail(text, s, e)
            if e - s < 2:
                continue
            span_text = text[s:e]
            # Отсекаем ложные PER: общие одушевлённые существительные («Арендатором» и т.п.)
            if not self._is_person_span(span_text):
                continue
            normalized = self.morph_normalizer.normalize(span_text, self.PERSON_TYPE)
            entities.append(ExtractedEntity(
                text=span_text,
                normalized=normalized,
                entity_type=self.PERSON_TYPE,
                start=s,
                end=e
            ))
        
        return entities

    def _extract_russian_names(self, text: str) -> List[ExtractedEntity]:
        entities = []
        # Смешанный регистр («Иванов») либо полный капс («ИВАНОВ»): ФИО капсом
        # в шапках и подписях — тоже персоны, их нельзя пропускать.
        words = list(re.finditer(r'\b[А-ЯЁ][а-яё]+\b|\b[А-ЯЁ]{2,}\b', text))
        n = len(words)

        for i in range(n):
            for size in (3, 2):
                if i + size > n:
                    continue
                ws = words[i:i + size]
                # Между словами имени — только пробелы (не запятые/дефисы и т.п.)
                if any(not text[a.end():b.start()].isspace() for a, b in zip(ws, ws[1:])):
                    continue
                candidate = ' '.join(m.group() for m in ws)
                if self._is_likely_person_name(candidate):
                    entities.append(ExtractedEntity(
                        text=candidate,
                        normalized=self.morph_normalizer.normalize(candidate, self.PERSON_TYPE),
                        entity_type=self.PERSON_TYPE,
                        start=ws[0].start(),
                        end=ws[-1].end()
                    ))
                    break

        for match in self._initials_pattern.finditer(text):
            s0, e0 = self._trim_person_tail(text, match.start(), match.end())
            if e0 - s0 < 2:
                continue
            name_text = text[s0:e0]
            # «И.О. Фамилия» / «М.П. Приложение» — бланк, а не человек:
            # после инициалов должно стоять слово с тегом Surn/Name/Patr.
            if not self._initials_span_has_name(name_text):
                continue
            normalized = self.morph_normalizer.normalize(name_text, self.PERSON_TYPE)
            entities.append(ExtractedEntity(
                text=name_text,
                normalized=normalized,
                entity_type=self.PERSON_TYPE,
                start=s0,
                end=e0
            ))
        
        return entities

    def _is_person_word(self, word: str) -> bool:
        """Слово является фамилией/именем/отчеством по pymorphy2.

        Одиночные буквы отсеиваются: pymorphy2 разбирает «И», «О», «В», «Е»,
        «М», «П» как имя/отчество (Sgtm,Name,Fixd,Abbr,Init), и без этой
        проверки персоной проходит любой инициал.
        """
        core = word.strip(_PERSON_STRIP)
        if not core:
            return False
        if '-' in core:
            # «Иванов-Ильин» — двойная фамилия, «Стороной-адресатом» — нет.
            # Части по дефису склеивать нельзя: разбор «Сторонойадресатом»
            # pymorphy2 считает именем, хотя исходное слово — нарицательное.
            parts = [p for p in core.split('-') if p]
            return bool(parts) and all(
                self._is_simple_person_token(p) for p in parts
            )
        return self._is_simple_person_token(core)

    def _is_simple_person_token(self, token: str) -> bool:
        """Разбор одного слова без дефисов."""
        letters = re.sub(r'[^А-Яа-яЁёA-Za-z]', '', token)
        if len(letters) < self.MIN_PERSON_WORD_LEN:
            return False
        low = letters.lower().replace('ё', 'е')
        if low in _PERSON_STOPWORDS:
            return False
        parsed = self.morph.parse(low)
        return any(
            'Surn' in str(p.tag) or 'Name' in str(p.tag) or 'Patr' in str(p.tag)
            for p in parsed
        )

    def _initials_span_has_name(self, text: str) -> bool:
        """Есть ли в спане с инициалами настоящее имя, а не слово-бланк.

        «И.О. Иванов» — да; «И.О. Фамилия», «М.П. Приложение» — нет.
        """
        for chunk in text.split():
            for word in re.findall(r'[А-ЯЁа-яё]+', chunk):
                if self._is_person_word(word):
                    return True
        return False

    def _is_likely_person_name(self, text: str) -> bool:
        words = text.split()
        if len(words) < 2 or len(words) > 3:
            return False
        
        for word in words:
            if not self._is_person_word(word):
                return False
        
        return True

    def _is_person_span(self, text: str) -> bool:
        """Хотя бы одно слово является фамилией/именем/отчеством по pymorphy.

        Отсекает одушевлённые нарицательные («Арендатором», «Директором»)
        и спаны, где «именем» выглядит лишь одиночная буква-инициал.
        """
        for word in text.split():
            if self._is_person_word(word):
                return True
        return False

    def _extract_regex(self, text: str) -> List[ExtractedEntity]:
        entities = []
        
        for entity_type, pattern in self._regex_patterns.items():
            for match in pattern.finditer(text):
                raw = match.group()
                if entity_type == "PHONE":
                    normalized = phone_canon(raw)
                else:
                    normalized = raw.lower()
                entities.append(ExtractedEntity(
                    text=raw,
                    normalized=normalized,
                    entity_type=entity_type,
                    start=match.start(),
                    end=match.end()
                ))
        
        return entities

    def _extract_known_orgs(self, text: str, extra_orgs: Optional[List[str]]) -> List[ExtractedEntity]:
        """Целевое извлечение организаций:
        — ФГУП «ДИД»/«Дирекция по инвестиционной деятельности» во всех вариантах написания;
        — организации, переданные вручную (например, вторая сторона договора).
        Общий NER-тег ORG не используется — он даёт ложные срабатывания на общую лексику.
        """
        entities = []
        
        for match in company_org_pattern().finditer(text):
            entities.append(ExtractedEntity(
                text=match.group(),
                normalized=DID_ORG_CANON,
                entity_type=self.ORG_TYPE,
                start=match.start(),
                end=match.end()
            ))
        
        for match in ROSUIM_PATTERN.finditer(text):
            entities.append(ExtractedEntity(
                text=match.group(),
                normalized=_ROSUIM_CANON,
                entity_type=self.ORG_TYPE,
                start=match.start(),
                end=match.end()
            ))
        
        for org in extra_orgs or []:
            if not org:
                continue
            pattern = self._org_query_regex(org)
            for match in pattern.finditer(text):
                entities.append(ExtractedEntity(
                    text=match.group(),
                    normalized=org_canon(org),
                    entity_type=self.ORG_TYPE,
                    start=match.start(),
                    end=match.end()
                ))
        
        return entities

    @staticmethod
    def _org_query_regex(org: str) -> object:
        # Кавычки («», "", “”) считаем взаимозаменяемыми (одним проходом)
        tokens = [
            re.sub(r'[«»"“”]', _QUOTE_CHARS, re.escape(t))
            for t in org.split()
        ]
        return re.compile(r'\s*'.join(tokens), re.IGNORECASE)

    def _looks_like_org_name(self, name: str) -> bool:
        """Похоже ли наименование на название организации, а не на текст договора.

        У настоящего контрагента есть ОПФ/аббревиатура, имя в кавычках либо
        это ФИО. Заголовок договора («Договора аренды … (Далее – Договор
        аренды)») не проходит ни одного условия и контрагентом не считается.
        """
        if _LEGAL_ENTITY_PATTERN.search(name) or _LEGAL_ABBREV_PATTERN.search(name):
            return True
        if _QUOTED_NAME.search(name):
            return True
        return self._is_likely_person_name(name)

    def _looks_like_org_alias(self, alias: str) -> bool:
        """Пригодно ли сокращение в скобках для подстановки во всём документе.

        «(ООО «Ромашка-Авто»)», «(Музей политической истории России)» — да.
        «(торгов)», «(далее)» — нет: это нарицательное слово, и подстановка
        превратила бы обычное слово документа в персону.
        """
        a = alias.strip()
        if len(a) < 4:
            return False
        if _LEGAL_ENTITY_PATTERN.search(a) or _LEGAL_ABBREV_PATTERN.search(a):
            return True
        words = re.findall(r'[А-ЯЁа-яё]+', a)
        if len(words) < 2:
            return False
        return sum(1 for w in words if w[:1].isupper()) >= 2

    def _extract_counterparty_orgs(self, text: str) -> List[ExtractedEntity]:
        """Контрагент по договору через оборот «…, именуемое в дальнейшем «Роль»».

        ДИД уже маскируется отдельным паттерном, поэтому повторные захваты с ним
        пропускаем, чтобы не плодить второй плейсхолдер.
        Сокращённые имена в скобках «(Музей политической истории России)» тоже
        подставляются под тот же плейсхолдер во всех местах документа.
        """
        entities = []
        for m in _ORG_DECL_PATTERN.finditer(text):
            name = m.group(1).strip(' \t,;()')
            if len(name) < 4:
                continue
            if _ORG_DECL_REJECT.match(name):
                # Регуляторный реквизит перед «именуемое в дальнейшем…»:
                # «ИНН 7700000000, именуемое…» — не наименование контрагента.
                continue
            if company_org_pattern().search(name):
                continue
            if ROSUIM_PATTERN.search(name):
                continue
            if not self._looks_like_org_name(name):
                # Заголовок или иной общий текст договора, а не контрагент.
                continue
            # Тип определяем по всем словам: одиночное «имя», разобранное
            # как фамилия, не делает ФИО из заголовка договора.
            entity_type = (self.PERSON_TYPE
                           if self._is_likely_person_name(name)
                           else self.ORG_TYPE)
            normalized = org_canon(name)
            entities.append(ExtractedEntity(
                text=name,
                normalized=normalized,
                entity_type=entity_type,
                start=m.start(1),
                end=m.start(1) + len(name),
            ))
            for short in re.findall(r'\(([^()]{4,})\)', name):
                if not self._looks_like_org_alias(short):
                    continue
                pattern = self._org_query_regex(short)
                for sm in pattern.finditer(text):
                    entities.append(ExtractedEntity(
                        text=sm.group(),
                        normalized=normalized,
                        entity_type=entity_type,
                        start=sm.start(),
                        end=sm.end(),
                    ))
        return entities

    @staticmethod
    def _org_normalize(name: str) -> str:
        return re.sub(r'\s+', ' ', name).strip().lower()

    @staticmethod
    def _overlaps_any(entities: List[ExtractedEntity], s: int, e: int) -> bool:
        for ent in entities:
            if s < ent.end and ent.start < e:
                return True
        return False

    def _extract_legal_entities(self, text: str,
                                existing: List[ExtractedEntity]) -> List[ExtractedEntity]:
        """Наименования юрлиц: полная организационно-правовая форма + «Имя» и
        аббревиатура + «Имя». Полная и сокращённая формы одного лица получают
        общий плейсхолдер (дедупликация по имени в кавычках). Дальнейшие ссылки
        на «Имя» в кавычках маскируются тем же значением.

        ФГУП «ДИД», Росимущество и контрагент по договору уже извлечены выше и
        перекрывающиеся совпадения пропускаются, чтобы не плодить плейсхолдеры.
        """
        results: List[ExtractedEntity] = []
        seen_aliases: Set[Tuple[int, int]] = set()

        def add_alias(alias: str, key: str) -> None:
            """Все вхождения Quoted-имени получают тот же плейсхолдер."""
            for om in re.finditer(re.escape(alias), text):
                if (om.start(), om.end()) in seen_aliases:
                    continue
                seen_aliases.add((om.start(), om.end()))
                results.append(ExtractedEntity(
                    text=alias,
                    normalized=key,
                    entity_type=self.ORG_TYPE,
                    start=om.start(),
                    end=om.end(),
                ))

        matches = []
        for pattern in (_LEGAL_ENTITY_PATTERN, _LEGAL_ABBREV_PATTERN):
            matches.extend(pattern.finditer(text))

        for m in matches:
            name = m.group(2).strip()
            if len(name) < 2:
                continue
            s, e = m.start(), m.end()
            if self._overlaps_any(existing, s, e):
                continue
            normalized = org_canon(name)
            results.append(ExtractedEntity(
                text=text[s:e],
                normalized=normalized,
                entity_type=self.ORG_TYPE,
                start=s,
                end=e,
            ))
            add_alias(text[m.start(1):m.end(3)], normalized)
            # Сокращение сразу в скобках: «АО «Полное» («Сбербанк-АСТ»)» —
            # та же организация, поэтому получает тот же плейсхолдер.
            tail = _PAREN_ALIAS_PATTERN.match(text, e)
            if tail and org_canon(tail.group(1)) != normalized:
                add_alias(tail.group(1), normalized)

        return results

    def _merge_overlapping(self, entities: List[ExtractedEntity]) -> List[ExtractedEntity]:
        if not entities:
            return []
        
        sorted_entities = sorted(entities, key=lambda e: (e.start, -e.end))
        merged = [sorted_entities[0]]
        
        for current in sorted_entities[1:]:
            last = merged[-1]
            
            if current.start < last.end:
                if current.end > last.end:
                    if current.entity_type == last.entity_type:
                        last.end = current.end
            else:
                merged.append(current)
        
        return merged


def extract_entities(text: str, extra_orgs: Optional[List[str]] = None) -> List[ExtractedEntity]:
    extractor = NERExtractor()
    return extractor.extract(text, extra_orgs)