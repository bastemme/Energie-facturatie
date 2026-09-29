"""In-process view of the steps of running tasks.

Logs are written to the database when a task ends (see runner). So that the task page can show progress while
the agent works, the steps are also kept here, in memory, until the task ends. Only this process sees them; a
separate worker process shows the steps once the task has finished.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime

MAX_ENTRIES = 200


@dataclass(frozen=True)
class LiveEntry:
    created_at: datetime
    event: str
    message: str
    level: str
    data: dict | None
    duration_ms: int | None


_lock = threading.Lock()
_entries: dict[str, list[LiveEntry]] = {}


def append(task_id: str, entry: LiveEntry) -> None:
    with _lock:
        items = _entries.setdefault(task_id, [])
        if len(items) < MAX_ENTRIES:
            items.append(entry)


def entries(task_id: str) -> list[LiveEntry]:
    with _lock:
        return list(_entries.get(task_id, ()))


def total() -> int:
    with _lock:
        return sum(len(v) for v in _entries.values())


_progress: dict[str, tuple[int, int]] = {}


def set_progress(task_id: str, done: int, total: int) -> None:
    with _lock:
        _progress[task_id] = (done, total)


def progress(task_id: str) -> tuple[int, int] | None:
    with _lock:
        return _progress.get(task_id)


def clear(task_id: str) -> None:
    with _lock:
        _entries.pop(task_id, None)
        _progress.pop(task_id, None)
