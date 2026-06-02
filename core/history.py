"""In-memory история запросов по модулям."""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from config import HISTORY_SIZE


@dataclass
class HistoryEntry:
    timestamp: str
    query: str
    response: str
    extra: dict[str, Any] = field(default_factory=dict)


class ModuleHistory:
    """Кольцевой буфер последних N записей для одного модуля."""

    def __init__(self, module_name: str, max_size: int = HISTORY_SIZE):
        self.module_name = module_name
        self._entries: deque[HistoryEntry] = deque(maxlen=max_size)

    def add(self, query: str, response: str, **extra: Any) -> HistoryEntry:
        entry = HistoryEntry(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            query=query,
            response=response,
            extra=extra,
        )
        self._entries.append(entry)
        return entry

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "timestamp": e.timestamp,
                "query": e.query,
                "response": e.response,
                **e.extra,
            }
            for e in reversed(self._entries)
        ]

    def clear(self) -> None:
        self._entries.clear()


# Глобальные хранилища истории по модулям
economist_history = ModuleHistory("economist")
secretary_history = ModuleHistory("secretary")
lawyer_history = ModuleHistory("lawyer")
