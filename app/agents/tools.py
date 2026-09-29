"""Tool registry. Tools are the only way agents act on the world; each call is permission-checked and logged."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    permission: str
    handler: Callable[..., Any] | None = None  # None = declared, not implemented yet
    summarize: Callable[[Any], dict] | None = None  # compact, loggable summary of the result
    needs_db: bool = False  # handler receives the task's database session as `db`

    @property
    def implemented(self) -> bool:
        return self.handler is not None


_TOOLS: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    _TOOLS[spec.name] = spec
    return spec


def get_tool(name: str) -> ToolSpec | None:
    _ensure_loaded()
    return _TOOLS.get(name)


def all_tools() -> dict[str, ToolSpec]:
    _ensure_loaded()
    return dict(_TOOLS)


def _ensure_loaded() -> None:
    import app.agents.toolbox  # noqa: F401  (registers tools)
