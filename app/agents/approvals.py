"""Human decisions on agent approval requests."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.context import add_log
from app.domain.enums import AgentStatus, ApprovalStatus, TaskStatus
from app.models import AgentApproval, AgentRecord, User
from app.models.base import utcnow
from app.services.audit import audit


class ApprovalError(Exception):
    pass


def decide(db: Session, approval: AgentApproval, user: User, *, approve: bool, note: str | None) -> None:
    if approval.status != ApprovalStatus.PENDING:
        raise ApprovalError("Over dit verzoek is al besloten.")
    if not approve and not (note or "").strip():
        raise ApprovalError("Geef een toelichting bij een afwijzing.")
    approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    approval.decided_by_id = user.id
    approval.decided_at = utcnow()
    approval.decision_note = (note or "").strip() or None
    task = approval.task
    record = db.get(AgentRecord, approval.agent_id)
    if task.status == TaskStatus.WAITING_APPROVAL:
        if approve:
            task.status = TaskStatus.QUEUED  # the agent resumes and finds the approval
            task.not_before = None
        else:
            task.status = TaskStatus.CANCELLED
            task.error = f"Goedkeuring afgewezen door {user.email}: {approval.decision_note}"
            task.finished_at = utcnow()
    if record and record.status == AgentStatus.WAITING:
        record.status = AgentStatus.IDLE
        record.current_task_id = None
    add_log(db, task.id, approval.agent_id, "approval.decided",
            f"{'Goedgekeurd' if approve else 'Afgewezen'} door {user.email}"
            + (f": {approval.decision_note}" if approval.decision_note else ""),
            level="INFO" if approve else "WARNING", data={"action": approval.action})
    audit(db, "agent.approval_decided", user=user, object_type="agent_approval", object_id=approval.id,
          details={"agent": approval.agent_id, "action": approval.action, "approved": approve})
