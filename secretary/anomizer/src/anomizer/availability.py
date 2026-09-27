"""Проверка готовности модуля обезличивания.

Зависимости NER (pymorphy2, natasha) нужны не при импорте, а при создании
`MorphAnalyzer()` — то есть уже во время запроса. Поэтому проверка выполняется
отдельно и позволяет отдать 503 с понятным текстом вместо 500
«Ошибка обработки: No module named 'pkg_resources'».

Модели Natasha вшиты в пакет (natasha/data), скачивание в рантайме не требуется,
поэтому проверка остаётся дешёвой: импортируются только лёгкие модули верхнего
уровня, без `NewsEmbedding()`.
"""
from __future__ import annotations

import importlib
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# (имя модуля для импорта, имя пакета в pip, человекочитаемое пояснение)
_REQUIRED: Tuple[Tuple[str, str, str], ...] = (
    (
        "pymorphy2",
        "pymorphy2 pymorphy2-dicts-ru",
        "морфология для распознавания имён",
    ),
    ("natasha", "natasha", "распознавание персон и организаций"),
    (
        "pkg_resources",
        "setuptools<81",
        "доступ к метаданным пакетов для pymorphy2",
    ),
)

INSTALL_HINT = "pip install " + " ".join(pkg for _, pkg, _ in _REQUIRED)


def missing_dependencies() -> List[str]:
    """Пакеты, которых не хватает для работы обезличивания.

    Пустой список — модуль готов к работе. Импорты уже загруженных модулей
    почти бесплатны (lookup в sys.modules), поэтому проверку можно выполнять
    на каждый запрос.
    """
    missing: List[str] = []
    for module, package, purpose in _REQUIRED:
        try:
            importlib.import_module(module)
        except Exception as exc:
            missing.append(f"{package} ({purpose}) — {exc.__class__.__name__}: {exc}")
    return missing


def unavailable_message(missing: List[str]) -> str:
    """Текст для пользователя: чего не хватает и что делать."""
    return (
        "Модуль обезличивания временно недоступен: на сервере не установлены "
        "зависимости. Остальные модули работают штатно. "
        "Администратору: " + INSTALL_HINT + ". Не установлено: "
        + "; ".join(missing)
    )


def ensure_available() -> None:
    """Поднять HTTPException(503), если зависимостей не хватает.

    Бросает fastapi.HTTPException, поэтому модуль можно использовать прямо
    из обработчика запроса. Вне FastAPI поднимется RuntimeError.
    """
    missing = missing_dependencies()
    if not missing:
        return

    from fastapi import HTTPException

    message = unavailable_message(missing)
    logger.warning("Обезличивание недоступно: %s", "; ".join(missing))
    raise HTTPException(status_code=503, detail=message)
