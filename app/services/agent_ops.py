"""Read models for the AI Operations dashboard."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import live
from app.agents.catalog import AGENTS
from app.domain.enums import AgentStatus, ApprovalStatus, TaskStatus
from app.models import AgentApproval, AgentLog, AgentRecord, AgentTask

GROUP_ORDER = ["Coördinatie", "Acquisitie", "Facturen", "Terugvordering"]
CATALOG_ORDER = {a.id: i for i, a in enumerate(AGENTS)}
OPEN = (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)


@dataclass
class AgentCard:
    agent: AgentRecord
    state: str  # running | idle | waiting | failed | disabled | planned
    current_task: AgentTask | None
    queued: int


def agent_state(a: AgentRecord) -> str:
    if not a.implemented:
        return "planned"
    if not a.enabled:
        return "disabled"
    return {AgentStatus.RUNNING: "running", AgentStatus.WAITING: "waiting",
            AgentStatus.FAILED: "failed"}.get(a.status, "idle")


def dashboard(db: Session) -> dict:
    agents = db.scalars(select(AgentRecord)).all()
    open_tasks = db.scalars(select(AgentTask).where(AgentTask.status.in_(OPEN))
                            .order_by(AgentTask.priority.desc(), AgentTask.created_at)).all()
    queued_per_agent = Counter(t.agent_id for t in open_tasks if t.status == TaskStatus.QUEUED)
    tasks_by_id = {t.id: t for t in open_tasks}
    cards = [AgentCard(a, agent_state(a), tasks_by_id.get(a.current_task_id) if a.current_task_id else None,
                       queued_per_agent[a.id]) for a in agents]
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    cards.sort(key=lambda c: (order.get(c.agent.group, 9), not c.agent.implemented, CATALOG_ORDER.get(c.agent.id, 99)))
    groups: dict[str, list[AgentCard]] = {}
    for c in cards:
        groups.setdefault(c.agent.group, []).append(c)
    states = Counter(c.state for c in cards)
    approvals = db.scalars(select(AgentApproval).where(AgentApproval.status == ApprovalStatus.PENDING)
                           .order_by(AgentApproval.requested_at)).all()
    recent = db.scalars(select(AgentTask).where(AgentTask.status.in_(
        (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED))).order_by(AgentTask.finished_at.desc())
        .limit(12)).all()
    errors = db.scalars(select(AgentTask).where(AgentTask.status == TaskStatus.FAILED)
                        .order_by(AgentTask.finished_at.desc()).limit(6)).all()
    runnable = {a.id for a in agents if a.implemented and a.enabled}
    return {
        "groups": groups, "names": {a.id: a.name for a in agents},
        "counts": {"total": len(agents), "operational": sum(1 for a in agents if a.implemented),
                   "running": states["running"], "idle": states["idle"] + states["waiting"],
                   "failed": states["failed"], "planned": states["planned"], "disabled": states["disabled"],
                   "queued": sum(1 for t in open_tasks if t.status == TaskStatus.QUEUED),
                   "approvals": len(approvals)},
        "running": [t for t in open_tasks if t.status == TaskStatus.RUNNING],
        "queued": [t for t in open_tasks if t.status == TaskStatus.QUEUED],
        "waiting": [t for t in open_tasks if t.status == TaskStatus.WAITING_APPROVAL],
        "runnable": runnable, "approvals": approvals, "recent": recent, "errors": errors,
    }


def pulse(db: Session) -> str:
    """Cheap change signature for live refresh."""
    last_log = db.scalar(select(func.max(AgentLog.created_at)))
    open_count = db.scalar(select(func.count()).select_from(AgentTask).where(AgentTask.status.in_(OPEN)))
    pending = db.scalar(select(func.count()).select_from(AgentApproval).where(
        AgentApproval.status == ApprovalStatus.PENDING))
    return f"{last_log}|{open_count}|{pending}|{live.total()}"
