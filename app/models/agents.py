"""AI Operations: agents, tasks, logs, messages, approvals, shared context and researched prospects.

Agent identity (name, role, instructions, permissions, tools) is defined in code (app/agents/catalog.py) and
synced into `agents` on startup; runtime state lives here so the dashboard and every process see the same truth.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.domain.enums import AgentStatus, ApprovalStatus, LeadStage, ProspectStatus, Qualification, TaskStatus
from app.models.base import IdMixin, TimestampMixin, enum_type, utcnow


class AgentRecord(TimestampMixin, Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # stable slug, e.g. "lead_researcher"
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(200))
    group: Mapped[str] = mapped_column(String(40))
    system_instructions: Mapped[str] = mapped_column(Text)
    permissions: Mapped[list] = mapped_column(JSON)
    tools: Mapped[list] = mapped_column(JSON)
    definition_version: Mapped[str] = mapped_column(String(16))
    implemented: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[AgentStatus] = mapped_column(enum_type(AgentStatus), default=AgentStatus.IDLE)
    current_task_id: Mapped[str | None] = mapped_column(String(36))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class AgentTask(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_tasks"

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[TaskStatus] = mapped_column(enum_type(TaskStatus), default=TaskStatus.QUEUED, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)  # 1 (low) … 9 (urgent)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("agent_tasks.id", ondelete="SET NULL"), index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(64))
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_agent_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    number: Mapped[int | None] = mapped_column(Integer, index=True)  # human-friendly, e.g. #1042
    progress_done: Mapped[int | None] = mapped_column(Integer)
    progress_total: Mapped[int | None] = mapped_column(Integer)

    logs: Mapped[list["AgentLog"]] = relationship(back_populates="task", cascade="all, delete-orphan",
                                                  order_by="AgentLog.created_at")

    @property
    def duration_ms(self) -> int | None:
        if self.started_at and self.finished_at:
            return int((self.finished_at - self.started_at).total_seconds() * 1000)
        return None

    @property
    def is_open(self) -> bool:
        return self.status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)


class AgentLog(IdMixin, Base):
    """Execution log: every step, tool call, permission check and error of a task. No secrets, no invoice content."""

    __tablename__ = "agent_logs"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("agent_tasks.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(10), default="INFO")  # INFO, WARNING, ERROR
    event: Mapped[str] = mapped_column(String(64))  # task.started, tool.call, handoff, approval.requested, …
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSON)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    task: Mapped[AgentTask | None] = relationship(back_populates="logs")


class AgentMessage(IdMixin, Base):
    """Agent-to-agent communication. A handoff message creates a task for the recipient."""

    __tablename__ = "agent_messages"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    from_agent_id: Mapped[str] = mapped_column(String(64), index=True)
    to_agent_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # HANDOFF, REQUEST, RESULT, NOTE
    subject: Mapped[str] = mapped_column(String(300))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    source_task_id: Mapped[str | None] = mapped_column(ForeignKey("agent_tasks.id", ondelete="SET NULL"))
    created_task_id: Mapped[str | None] = mapped_column(ForeignKey("agent_tasks.id", ondelete="SET NULL"))


class AgentApproval(IdMixin, Base):
    """Human-in-the-loop gate. A task that needs approval pauses until a staff member decides."""

    __tablename__ = "agent_approvals"
    __table_args__ = (UniqueConstraint("task_id", "action", name="uq_approval_task_action"),)

    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[ApprovalStatus] = mapped_column(enum_type(ApprovalStatus), default=ApprovalStatus.PENDING,
                                                   index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)

    task: Mapped[AgentTask] = relationship()


class SharedContext(IdMixin, Base):
    """Shared memory between agents, scoped (global, workflow:<run id>, client:<id>)."""

    __tablename__ = "agent_context"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_context_scope_key"),)

    scope: Mapped[str] = mapped_column(String(80), index=True)
    key: Mapped[str] = mapped_column(String(120))
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    updated_by_agent_id: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Prospect(IdMixin, TimestampMixin, Base):
    """A company in the sales pipeline: found by research or via the website form. Shown as a lead (CRM
    pipeline fields) and as a company (firmographics). Personal contacts live in `contacts`."""

    __tablename__ = "prospects"

    company_name: Mapped[str] = mapped_column(String(200))
    sector: Mapped[str] = mapped_column(String(40), index=True)
    street: Mapped[str | None] = mapped_column(String(200))
    postcode: Mapped[str | None] = mapped_column(String(10))
    city: Mapped[str | None] = mapped_column(String(100))
    website: Mapped[str | None] = mapped_column(String(300))
    domain: Mapped[str | None] = mapped_column(String(200), index=True)
    phone: Mapped[str | None] = mapped_column(String(50))  # public business number only
    kvk_number: Mapped[str | None] = mapped_column(String(8))
    brand: Mapped[str | None] = mapped_column(String(120))  # chain brand, if a branch
    website_title: Mapped[str | None] = mapped_column(String(300))
    website_description: Mapped[str | None] = mapped_column(Text)
    signals: Mapped[dict | None] = mapped_column(JSON)
    fit_score: Mapped[int] = mapped_column(Integer, default=0)
    fit_reasons: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(40))  # openstreetmap, mock, …
    source_ref: Mapped[str | None] = mapped_column(String(80), index=True)  # e.g. osm:node/123
    evidence: Mapped[list] = mapped_column(JSON, default=list)  # [{label, url, retrieved_at}]
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[ProspectStatus] = mapped_column(enum_type(ProspectStatus), default=ProspectStatus.RESEARCHED)
    discovered_by_agent_id: Mapped[str | None] = mapped_column(String(64))
    research_task_id: Mapped[str | None] = mapped_column(ForeignKey("agent_tasks.id", ondelete="SET NULL"))
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"))
    # CRM pipeline (nullable so existing databases upgrade in place; NULL stage means NEW)
    stage_value: Mapped[LeadStage | None] = mapped_column("stage", enum_type(LeadStage), index=True,
                                                          default=LeadStage.NEW)
    qualification: Mapped[Qualification | None] = mapped_column(enum_type(Qualification))
    qualification_reasons: Mapped[list | None] = mapped_column(JSON)
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locations_count: Mapped[int | None] = mapped_column(Integer)
    relevance: Mapped[list | None] = mapped_column(JSON)  # [{text, source}]: why it may be relevant, with source
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action: Mapped[str | None] = mapped_column(String(300))
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    do_not_contact: Mapped[bool | None] = mapped_column(Boolean, default=False)
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)

    contacts: Mapped[list["Contact"]] = relationship(  # noqa: F821
        back_populates="prospect", cascade="all, delete-orphan", order_by="Contact.created_at")

    @property
    def stage(self) -> LeadStage:
        return self.stage_value or LeadStage.NEW

    @stage.setter
    def stage(self, value: LeadStage) -> None:
        self.stage_value = value

    @property
    def primary_contact(self):
        usable = [c for c in self.contacts if not c.do_not_contact]
        return max(usable, key=lambda c: (c.email is not None, c.confidence_rank, c.priority), default=None)
