"""In-memory история запросов по модулям (отдельно для каждой сессии браузера)."""
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from config import HISTORY_SIZE
from core.app_time import format_history_timestamp


@dataclass
class HistoryEntry:
    timestamp: str
    query: str
    response: str
    extra: dict[str, Any] = field(default_factory=dict)


class ModuleHistory:
    """Кольцевой буфер последних N записей на одну сессию."""

    def __init__(self, module_name: str, max_size: int = HISTORY_SIZE):
        self.module_name = module_name
        self._max_size = max_size
        self._by_session: dict[str, deque[HistoryEntry]] = defaultdict(
            lambda: deque(maxlen=self._max_size)
        )

    def add(self, session_id: str, query: str, response: str, **extra: Any) -> HistoryEntry:
        entry = HistoryEntry(
            timestamp=format_history_timestamp(),
            query=query,
            response=response,
            extra=extra,
        )
        self._by_session[session_id].append(entry)
        return entry

    def list(self, session_id: str) -> list[dict[str, Any]]:
        return [
            {
                "timestamp": e.timestamp,
                "query": e.query,
                "response": e.response,
                **e.extra,
            }
            for e in reversed(self._by_session[session_id])
        ]

    def clear(self, session_id: str) -> None:
        self._by_session.pop(session_id, None)


economist_history = ModuleHistory("economist")
secretary_history = ModuleHistory("secretary")
lawyer_history = ModuleHistory("lawyer")
