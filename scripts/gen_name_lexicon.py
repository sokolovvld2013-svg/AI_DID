"""Генерирует лексикон фамилий и имён для браузера -> static/js/lex_names.js

Зачем. Браузер считал фамилией любое слово на «-ов»/«-ин», поэтому в ФИО
попадали «Объектов», «Актов», «Расходов», «Арендаторов», «СПОРОВ». Сервер
отсекает их морфологией (pymorphy2), браузеру нужен тот же словарь.

Что кладём:
— номинативы ед. числа с тегами Surn/Name (падежные формы движок снимает
  сам окончанием, в словаре они не нужны);
— только те слова, у которых ЛУЧШИЙ разбор pymorphy2 — имя/фамилия. Иначе в
  лексикон попадают ложные друзья: «по» (предлог), «полы» (от «пол»);
— отчества не кладём: окончания -ович/-евич/-инична и так однозначны.

Проверяется в самом скрипте: «объектов», «актов», «расходов», «арендаторов»,
«споров», «один» отсутствуют; «иванов», «пушкин», «мирошникова» присутствуют.
«Хозяйчева» в словаре нет — pymorphy2 угадывает такие фамилии по окончанию,
их ловит якорь на инициалах в anonymize-core.js.

Запуск (из корня репозитория, нужен установленный natasha):
    python scripts/gen_name_lexicon.py static/js/lex_names.js
"""
import json
import re
import sys
import time

import natasha  # noqa: F401  патчит inspect.getargspec, иначе pymorphy2 не стартует
from pymorphy2 import MorphAnalyzer

OUT = sys.argv[1] if len(sys.argv) > 1 else 'lex_names.js'

morph = MorphAnalyzer()

NAME_TAGS = ('Surn', 'Name')
candidates = set()

t0 = time.perf_counter()
for unit in morph._units:
    ana = unit[0] if isinstance(unit, tuple) else unit
    dictionary = getattr(ana, 'dict', None)
    if dictionary is None or not hasattr(dictionary, 'iter_known_words'):
        continue
    for entry in dictionary.iter_known_words():
        word, tag = entry[0], entry[1]
        if not word or not word[0].isalpha():
            continue
        tags = set(re.split(r'[,\s]+', str(tag)))
        if 'sing' in tags and 'nomn' in tags and set(NAME_TAGS) & tags:
            candidates.add(word.replace('ё', 'е'))

# Лучший разбор решает: «по» — предлог, «пол» — имя, но «полы» — мн. число
# «пола», поэтому в лексикон попадает только «пол», а не «полы».
names = set()
for word in candidates:
    top = morph.parse(word)
    if not top:
        continue
    tags = set(re.split(r'[,\s]+', str(top[0].tag)))
    if set(NAME_TAGS) & tags:
        names.add(word)

# Прилагательные — названия и описания, не фамилии.
names = {w for w in names if not re.search(r'(?:ский|цкий)$', w)}

elapsed = time.perf_counter() - t0

with open(OUT, 'w', encoding='utf-8') as f:
    f.write('// Сгенерировано из словаря pymorphy2 (scripts/gen_name_lexicon.py).\n')
    f.write('// Правками не подлежит.\n')
    f.write('// Номинативы фамилий и имён, ед. число, только с лучшим разбором.\n')
    f.write('// Нужен, чтобы не считать фамилией любое слово на «-ов»/«-ин»\n')
    f.write('// («Объектов», «Актов», «Расходов», «СПОРОВ»).\n')
    f.write('(function (root) {\n')
    f.write("'use strict';\n")
    f.write('root.ANON_NAME_LEXICON = new Set(%s.split("\\n"));\n'
            % json.dumps('\n'.join(sorted(names)), ensure_ascii=False))
    f.write('})(typeof window !== "undefined" ? window : globalThis);\n')

size = len(open(OUT, 'rb').read()) // 1024
print('разбор словаря: %.1f с' % elapsed)
print('кандидатов: %d, отобрано: %d, файл: %d КБ' % (len(candidates), len(names), size))
missing = [w for w in ('иванов', 'пушкин', 'мирошникова', 'константинов', 'юсупов')
           if w not in names]
leaked = [w for w in ('по', 'полы', 'объектов', 'актов', 'расходов', 'арендаторов',
                      'споров', 'один', 'обысков', 'покупателя') if w in names]
print('нет настоящих фамилий:', missing or 'нет')
print('просочились нарицательные:', leaked or 'нет')
