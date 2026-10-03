from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class Entity:
    text: str
    normalized: str
    entity_type: str
    placeholder: str = ""
    positions: List[Tuple[int, int]] = field(default_factory=list)


class EntityRegistry:
    def __init__(self):
        self._entities: Dict[str, Entity] = {}
        self._canonical_map: Dict[str, str] = {}
        self._counters: Dict[str, int] = defaultdict(int)
        self._placeholders: Dict[str, str] = {}

    def _normalize_for_key(self, text: str, entity_type: str) -> str:
        return f"{entity_type}:{text.lower()}"

    def register(self, entity: Entity, start: int = None, end: int = None) -> str:
        canonical_key = self._normalize_for_key(entity.normalized, entity.entity_type)
        
        if canonical_key in self._canonical_map:
            existing_placeholder = self._canonical_map[canonical_key]
            existing_entity = self._entities[existing_placeholder]
            if start is not None and end is not None:
                existing_entity.positions.append((start, end))
            entity.placeholder = existing_placeholder
            return existing_placeholder
        
        self._counters[entity.entity_type] += 1
        placeholder = f"[{entity.entity_type}_{self._counters[entity.entity_type]}]"
        
        entity.placeholder = placeholder
        if start is not None and end is not None:
            entity.positions = [(start, end)]
        else:
            entity.positions = []
        self._entities[placeholder] = entity
        self._canonical_map[canonical_key] = placeholder
        self._placeholders[entity.text] = placeholder
        
        return placeholder

    def get_placeholder(self, text: str, entity_type: str, normalized: str) -> Optional[str]:
        canonical_key = self._normalize_for_key(normalized, entity_type)
        return self._canonical_map.get(canonical_key)

    def get_all_entities(self) -> List[Entity]:
        return list(self._entities.values())

    def reset(self):
        self._entities.clear()
        self._canonical_map.clear()
        self._counters.clear()
        self._placeholders.clear()


class RegexPatterns:
    EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    # Телефон в любом написании: с кодом страны (+7/8), со скобками вокруг
    # кода города и без кода вообще. Без разделителей номер не распознаётся,
    # иначе 10-значный ИНН принимался бы за телефон.
    PHONE = re.compile(
        r'(?<![\d-])'
        r'(?:'
        r'(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'
        r'|'
        r'\(\d{3}\)[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'
        r'|'
        r'\d{3}[\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2}'
        r')'
        r'(?![\d-])'
        r'(?:\s*(?:доб|доб\.|добление)\s*\d+)?'
        r'\b'
    )
    INN = re.compile(r'\b\d{10}\b|\b\d{12}\b|\b\d{13}\b')
    # Паспорт: серия (4 цифры) + номер (6 цифр) после подписи поля, либо просто
    # номер из 6-8 цифр (загранпаспорт без серии: «Паспорт: № 25382441»).
    # Первая группа — сами цифры, маскируется только она («паспорт серия 50 20
    # № 123456» → подпись остаётся, меняются цифры). Между серией и номером
    # допускается разделитель «№»/«номер»/«N», как в полях бланков.
    PASSPORT = re.compile(
        r'(?:паспорт[а-яё]*|удостоверени[ея]\s+личности)[^\n]{0,20}?'
        r'(\d{2}[ ]?\d{2}(?:[ ]?(?:№|номер[а-яё]*|N\.?))?[ ]?\d{6}'
        r'|[№N]?\s*\d{6,8})(?!\d)',
        re.IGNORECASE
    )
    # Персональный номер (зарубежный идентификатор: «Персональный номер:
    # 39307150323»). Маскируется только группа цифр, подпись поля остаётся.
    PERSONAL_NUM = re.compile(
        r'персональн[а-яё]*\s+номер[а-яё]*[^\n]{0,10}?(\d(?:[ ]?\d){10})(?!\d)',
        re.IGNORECASE
    )