"""Agent abstraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agents.context import ExecutionContext


@dataclass(frozen=True)
class AgentSpec:
    """Identity and configuration of an agent. Defined in code, synced to the `agents` table."""

    id: str
    name: str
    description: str
    role: str
    group: str
    system_instructions: str
    permissions: tuple[str, ...]
    tools: tuple[str, ...]
    task_types: tuple[str, ...] = ()
    version: str = "1.0"
    approval_actions: tuple[str, ...] = field(default=())  # actions this agent must get approved


class Agent:
    """Base class for executable agents. Subclasses implement `run` and return a JSON-serialisable output."""

    spec: AgentSpec

    def run(self, ctx: ExecutionContext) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


class AgentError(Exception):
    """Business-level failure with a message that is safe to show in the dashboard."""


class RetryableError(AgentError):
    """Temporary failure (network, rate limit). The runner retries with backoff."""


class ApprovalPending(Exception):  # noqa: N818 - control flow signal, not an error
    def __init__(self, approval_id: str):
        super().__init__("Wacht op goedkeuring")
        self.approval_id = approval_id


class ApprovalRejected(Exception):  # noqa: N818
    def __init__(self, note: str | None):
        super().__init__(note or "Afgewezen")
        self.note = note
