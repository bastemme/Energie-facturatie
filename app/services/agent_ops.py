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

GROUP_ORDER = ["Acquisitie", "Facturen", "Terugvordering", "Coördinatie"]
CATALOG_ORDER = {a.id: i for i, a in enumerate(AGENTS)}
OPEN = (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)


@dataclass
class AgentCard:
    agent: AgentRecord
    state: str  # running | queued | idle | waiting (for approval) | failed | disabled | planned
    current_task: AgentTask | None
    queued: int
    completed: int = 0
    failed: int = 0
    last_activity: AgentLog | None = None


def agent_state(a: AgentRecord, queued: int = 0) -> str:
    """RUNNING / WAITING (work queued) / IDLE / AWAITING_APPROVAL / ERROR, plus disabled and not-yet-built."""
    if not a.implemented:
        return "planned"
    if not a.enabled:
        return "disabled"
    state = {AgentStatus.RUNNING: "running", AgentStatus.WAITING: "waiting",
             AgentStatus.FAILED: "failed"}.get(a.status, "idle")
    return "queued" if state == "idle" and queued else state


def dashboard(db: Session) -> dict:
    agents = db.scalars(select(AgentRecord)).all()
    open_tasks = db.scalars(select(AgentTask).where(AgentTask.status.in_(OPEN))
                            .order_by(AgentTask.priority.desc(), AgentTask.created_at)).all()
    queued_per_agent = Counter(t.agent_id for t in open_tasks if t.status == TaskStatus.QUEUED)
    tasks_by_id = {t.id: t for t in open_tasks}
    done = dict(db.execute(select(AgentTask.agent_id, func.count()).where(AgentTask.status == TaskStatus.COMPLETED)
                           .group_by(AgentTask.agent_id)).all())
    fails = dict(db.execute(select(AgentTask.agent_id, func.count()).where(AgentTask.status == TaskStatus.FAILED)
                            .group_by(AgentTask.agent_id)).all())
    last_ids = dict(db.execute(select(AgentLog.agent_id, func.max(AgentLog.created_at)).group_by(AgentLog.agent_id))
                    .all())
    last_logs = {}
    for agent_id, ts in last_ids.items():
        last_logs[agent_id] = db.scalar(select(AgentLog).where(AgentLog.agent_id == agent_id,
                                                               AgentLog.created_at == ts).limit(1))
    cards = [AgentCard(a, agent_state(a, queued_per_agent[a.id]),
                       tasks_by_id.get(a.current_task_id) if a.current_task_id else None, queued_per_agent[a.id],
                       done.get(a.id, 0), fails.get(a.id, 0), last_logs.get(a.id)) for a in agents]
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
                   "running": states["running"], "idle": states["idle"] + states["queued"],
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
    from app.models import ProspectEvent

    last_event = db.scalar(select(func.max(ProspectEvent.created_at)))
    return f"{last_log}|{last_event}|{open_count}|{pending}|{live.total()}"


# ---------------------------------------------------------------- lead-generation pipeline (live view)


@dataclass
class PipelineStep:
    key: str
    label: str
    actor: str  # agent id, or "human"
    count: int
    note: str
    href: str
    active: bool = False  # an agent is working on this step right now


def pipeline(db: Session) -> list[PipelineStep]:
    from app.domain.enums import LeadStage, OutreachStatus, Qualification
    from app.models import Contact, InboundMessage, OutreachMessage, Prospect

    def n(stmt) -> int:
        return db.scalar(stmt) or 0

    running = set(db.scalars(select(AgentTask.agent_id).where(AgentTask.status == TaskStatus.RUNNING)).all())
    count = select(func.count())
    steps = [
        PipelineStep("research", "Onderzoek", "lead_researcher", n(count.select_from(Prospect)), "leads gevonden",
                     "/app/leads"),
        PipelineStep("qualify", "Kwalificatie", "lead_qualifier", n(count.select_from(Prospect).where(
            Prospect.qualification.in_((Qualification.STRONG, Qualification.GOOD)))), "gekwalificeerd",
            "/app/leads?qualification=good"),
        PipelineStep("contacts", "Contacten", "contact_researcher", n(select(func.count(func.distinct(
            Contact.prospect_id))).where(Contact.email.is_not(None))), "met e-mailadres", "/app/contacts"),
        PipelineStep("draft", "Concepten", "outreach", n(count.select_from(OutreachMessage)),
                     "opgesteld", "/app/outreach?tab=approval"),
        PipelineStep("approval", "Goedkeuring", "human", n(count.select_from(OutreachMessage).where(
            OutreachMessage.status == OutreachStatus.PENDING_APPROVAL)), "wachten op u",
            "/app/outreach?tab=approval"),
        PipelineStep("send", "Verzonden", "email", n(count.select_from(OutreachMessage).where(
            OutreachMessage.status == OutreachStatus.SENT)), "verstuurd", "/app/outreach?tab=sent"),
        PipelineStep("reply", "Reacties", "email", n(count.select_from(InboundMessage)), "geclassificeerd",
                     "/app/inbox"),
        PipelineStep("meeting", "Gesprek", "human", n(count.select_from(Prospect).where(
            Prospect.stage_value.in_((LeadStage.MEETING, LeadStage.PILOT, LeadStage.CUSTOMER)))), "leads",
            "/app/leads?stage=MEETING"),
    ]
    for s in steps:
        s.active = s.actor in running
    return steps


@dataclass
class Activity:
    at: object
    actor: str  # display name
    actor_kind: str  # agent | human | system
    message: str
    prospect_id: str | None
    company: str | None
    task_id: str | None


def activity(db: Session, limit: int = 30) -> list[Activity]:
    """Everything that happened in the pipeline, newest first: agent actions and human decisions."""
    from app.models import Prospect, ProspectEvent, User

    names = {a.id: a.name for a in AGENTS}
    events = db.scalars(select(ProspectEvent).order_by(ProspectEvent.created_at.desc()).limit(limit)).all()
    companies = dict(db.execute(select(Prospect.id, Prospect.company_name).where(
        Prospect.id.in_({e.prospect_id for e in events}))).all()) if events else {}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({e.user_id for e in events if e.user_id})))}
    out = []
    for e in events:
        if e.user_id:
            actor, kind = f"Mens · {users[e.user_id].full_name or users[e.user_id].email}" \
                if e.user_id in users else "Mens", "human"
        elif e.agent_id and e.agent_id in names:
            actor, kind = names[e.agent_id], "agent"
        else:
            actor, kind = "Systeem", "system"
        out.append(Activity(e.created_at, actor, kind, e.message, e.prospect_id, companies.get(e.prospect_id),
                            e.task_id))
    # Task results of every agent (invoices, recovery, reports) and human approval decisions.
    for log in db.scalars(select(AgentLog).where(AgentLog.event.in_(("task.completed", "task.failed",
                                                                      "approval.decided")))
                          .order_by(AgentLog.created_at.desc()).limit(limit)):
        human = log.event == "approval.decided"
        out.append(Activity(log.created_at, "Mens" if human else names.get(log.agent_id, log.agent_id),
                            "human" if human else "agent",
                            ("Mislukt: " if log.event == "task.failed" else "") + log.message, None, None,
                            log.task_id))
    out.sort(key=lambda a: _aware(a.at), reverse=True)
    return out[:limit]


def _aware(ts):
    from datetime import UTC

    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
