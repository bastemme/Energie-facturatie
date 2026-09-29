"""Task queue (database-backed, so every process and the dashboard share it)."""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.domain.enums import TaskStatus
from app.models import AgentRecord, AgentTask
from app.models.base import utcnow


def enqueue(db: Session, agent_id: str, task_type: str, payload: dict, *, title: str, priority: int = 5,
            parent: AgentTask | None = None, workflow_id: str | None = None, client_id: str | None = None,
            created_by_user_id: str | None = None, created_by_agent_id: str | None = None,
            max_attempts: int = 2) -> AgentTask:
    from app.agents.registry import get_spec

    spec = get_spec(agent_id)
    if spec is None:
        raise ValueError(f"Onbekende agent: {agent_id}")
    if spec.task_types and task_type not in spec.task_types:
        raise ValueError(f"{spec.name} accepteert geen taak van type '{task_type}'")
    task = AgentTask(
        agent_id=agent_id, task_type=task_type, title=title[:300], input=payload, priority=max(1, min(9, priority)),
        parent_task_id=parent.id if parent else None,
        workflow_id=workflow_id or (parent.workflow_id if parent else None),
        workflow_run_id=(parent.workflow_run_id if parent and parent.workflow_run_id else None) or str(uuid.uuid4()),
        client_id=client_id or (parent.client_id if parent else None),
        created_by_user_id=created_by_user_id, created_by_agent_id=created_by_agent_id, max_attempts=max_attempts,
    )
    db.add(task)
    db.flush()
    return task


def claim_next(db: Session) -> AgentTask | None:
    """Atomically move the next runnable task to RUNNING. Safe with several workers."""
    now = utcnow()
    runnable_agents = select(AgentRecord.id).where(AgentRecord.enabled.is_(True), AgentRecord.implemented.is_(True))
    candidates = db.scalars(
        select(AgentTask).where(
            AgentTask.status == TaskStatus.QUEUED, AgentTask.agent_id.in_(runnable_agents),
            or_(AgentTask.not_before.is_(None), AgentTask.not_before <= now),
        ).order_by(AgentTask.priority.desc(), AgentTask.created_at).limit(5)
    ).all()
    for task in candidates:
        result = db.execute(
            update(AgentTask).where(and_(AgentTask.id == task.id, AgentTask.status == TaskStatus.QUEUED))
            .values(status=TaskStatus.RUNNING, started_at=now, attempts=AgentTask.attempts + 1)
        )
        db.commit()
        if result.rowcount == 1:
            db.refresh(task)
            return task
    return None


def cancel(db: Session, task: AgentTask, reason: str) -> None:
    if not task.is_open:
        raise ValueError("Deze taak is al afgerond.")
    task.status = TaskStatus.CANCELLED
    task.error = reason
    task.finished_at = utcnow()


def retry(db: Session, task: AgentTask) -> None:
    if task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
        raise ValueError("Alleen mislukte of geannuleerde taken kunnen opnieuw worden gestart.")
    task.status = TaskStatus.QUEUED
    task.error = None
    task.not_before = None
    task.finished_at = None
    task.max_attempts = task.attempts + 2
