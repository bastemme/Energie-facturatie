"""Execution context handed to an agent: its task, permissions, tools, shared context, logging, approvals
and handoffs. Everything an agent does goes through here, so it is permission-checked and logged."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import live
from app.agents.base import AgentSpec, ApprovalPending, ApprovalRejected
from app.agents.permissions import ALWAYS_APPROVE, PermissionDenied, require
from app.agents.tools import get_tool
from app.domain.enums import ApprovalStatus
from app.models import AgentApproval, AgentLog, AgentMessage, AgentTask, SharedContext
from app.models.base import utcnow


def add_log(db: Session, task_id: str | None, agent_id: str, event: str, message: str, *, level: str = "INFO",
            data: dict | None = None, duration_ms: int | None = None) -> None:
    db.add(AgentLog(task_id=task_id, agent_id=agent_id, event=event, message=message[:2000], level=level,
                    data=data, duration_ms=duration_ms))


class SharedContextAccess:
    def __init__(self, ctx: ExecutionContext, scope: str):
        self.ctx, self.scope = ctx, scope

    def get(self, key: str, default: Any = None) -> Any:
        self.ctx.require("context.read")
        row = self.ctx.db.scalar(select(SharedContext).where(SharedContext.scope == self.scope,
                                                             SharedContext.key == key))
        return row.value if row else default

    def set(self, key: str, value: Any) -> None:
        self.ctx.require("context.write")
        row = self.ctx.db.scalar(select(SharedContext).where(SharedContext.scope == self.scope,
                                                             SharedContext.key == key))
        if row is None:
            row = SharedContext(scope=self.scope, key=key)
            self.ctx.db.add(row)
        row.value = value
        row.updated_by_agent_id = self.ctx.agent.id
        row.updated_at = utcnow()
        self.ctx.log("context.write", f"Gedeelde context bijgewerkt: {self.scope}/{key}")


class ExecutionContext:
    def __init__(self, db: Session, agent: AgentSpec, task: AgentTask):
        self.db, self.agent, self.task = db, agent, task
        self.input: dict = dict(task.input or {})
        # Logs are buffered and written by the runner after the task's transaction ends, so they are kept even
        # when the agent's work is rolled back (and SQLite never sees two concurrent writers).
        self.buffered_logs: list[AgentLog] = []

    # ------------------------------------------------------------------ logging & permissions
    def log(self, event: str, message: str, *, level: str = "INFO", data: dict | None = None,
            duration_ms: int | None = None) -> None:
        entry = AgentLog(task_id=self.task.id, agent_id=self.agent.id, event=event, message=message[:2000],
                         level=level, data=data, duration_ms=duration_ms, created_at=utcnow())
        self.buffered_logs.append(entry)
        live.append(self.task.id, live.LiveEntry(entry.created_at, event, entry.message, level, data, duration_ms))

    def require(self, permission: str) -> None:
        try:
            require(self.agent.permissions, permission)
        except PermissionDenied as exc:
            self.log("permission.denied", str(exc), level="ERROR", data={"permission": permission})
            raise

    # ------------------------------------------------------------------ tools
    def use(self, tool_name: str, **kwargs) -> Any:
        tool = get_tool(tool_name)
        if tool is None or tool_name not in self.agent.tools:
            self.log("permission.denied", f"Tool '{tool_name}' is niet beschikbaar voor deze agent", level="ERROR")
            raise PermissionDenied(f"Tool '{tool_name}' is niet toegewezen aan {self.agent.name}")
        self.require(tool.permission)
        if not tool.implemented:
            raise PermissionDenied(f"Tool '{tool_name}' is nog niet geïmplementeerd")
        if tool.needs_db:
            kwargs["db"] = self.db
        started = time.perf_counter()
        try:
            result = tool.handler(**kwargs)
        except Exception as exc:
            self.log("tool.error", f"{tool_name}: {exc}", level="WARNING", data={"tool": tool_name,
                     "error": type(exc).__name__}, duration_ms=int((time.perf_counter() - started) * 1000))
            raise
        summary = tool.summarize(result) if tool.summarize else {}
        self.log("tool.call", f"{tool_name}", data={"tool": tool_name, "args": _safe_args(kwargs), **summary},
                 duration_ms=int((time.perf_counter() - started) * 1000))
        return result

    # ------------------------------------------------------------------ shared context
    def shared(self, scope: str = "global") -> SharedContextAccess:
        if scope == "workflow":
            scope = f"workflow:{self.task.workflow_run_id or self.task.id}"
        elif scope == "client":
            if not self.task.client_id:
                raise PermissionDenied("Deze taak hoort niet bij een klant")
            scope = f"client:{self.task.client_id}"
        return SharedContextAccess(self, scope)

    # ------------------------------------------------------------------ approvals
    def require_approval(self, action: str, summary: str, details: dict | None = None) -> None:
        """Returns if a human approved `action` for this task; otherwise pauses the task."""
        approval = self.db.scalar(select(AgentApproval).where(AgentApproval.task_id == self.task.id,
                                                              AgentApproval.action == action))
        if approval is not None and approval.status == ApprovalStatus.APPROVED:
            self.log("approval.granted", f"Goedkeuring aanwezig voor '{action}'")
            return
        if approval is not None and approval.status == ApprovalStatus.REJECTED:
            raise ApprovalRejected(approval.decision_note)
        if approval is None:
            approval = AgentApproval(task_id=self.task.id, agent_id=self.agent.id, action=action, summary=summary,
                                     details=details)
            self.db.add(approval)
            self.db.flush()
            self.log("approval.requested", summary, data={"action": action, "approval_id": approval.id})
        raise ApprovalPending(approval.id)

    def guard_sensitive(self, permission: str, summary: str, details: dict | None = None) -> None:
        """Sensitive actions (e-mail, claims) always need a human decision, whatever the agent config says."""
        self.require(permission)
        if permission in ALWAYS_APPROVE or permission in self.agent.approval_actions:
            self.require_approval(permission, summary, details)

    # ------------------------------------------------------------------ agent-to-agent
    def handoff(self, to_agent: str, task_type: str, payload: dict, *, title: str, subject: str | None = None,
                priority: int | None = None) -> AgentTask:
        from app.agents.queue import enqueue
        from app.agents.registry import get_spec

        self.require("tasks.handoff")
        target = get_spec(to_agent)
        if target is None:
            raise PermissionDenied(f"Onbekende agent: {to_agent}")
        child = enqueue(self.db, to_agent, task_type, payload, title=title, priority=priority or self.task.priority,
                        parent=self.task, created_by_agent_id=self.agent.id)
        self.db.add(AgentMessage(from_agent_id=self.agent.id, to_agent_id=to_agent, kind="HANDOFF",
                                 subject=subject or title, payload=payload, source_task_id=self.task.id,
                                 created_task_id=child.id))
        self.log("handoff", f"Overgedragen aan {target.name}: {title}", data={"to": to_agent, "task_id": child.id})
        return child


def _safe_args(kwargs: dict) -> dict:
    out = {}
    for k, v in kwargs.items():
        if k == "db":
            continue
        if isinstance(v, str | int | float | bool) or v is None:
            out[k] = v if not isinstance(v, str) else v[:200]
        elif isinstance(v, list | tuple):
            out[k] = [str(x)[:80] for x in v][:10]
        else:
            out[k] = type(v).__name__
    return out
