"""Business dashboard: sales pipeline and recovery money in one view.

All figures are counted from the database. Records created as demo or test data are counted separately, so
the UI can say how much of a figure is demonstration material. Potential, verified and recovered money are
never added together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import (
    ApprovalStatus,
    LeadStage,
    OutreachStatus,
    Qualification,
    ReplyCategory,
    TaskStatus,
)
from app.domain.money import ZERO
from app.models import (
    AgentApproval,
    AgentTask,
    Anomaly,
    Client,
    Contact,
    Document,
    InboundMessage,
    OutreachMessage,
    Prospect,
    RecoveryCase,
)
from app.models.base import utcnow
from app.services.metrics import Summary, summary

POSITIVE = (ReplyCategory.INTERESTED, ReplyCategory.MEETING_REQUEST, ReplyCategory.MORE_INFORMATION)
CONTACTED_STAGES = (LeadStage.CONTACTED, LeadStage.RESPONDED, LeadStage.MEETING, LeadStage.PILOT, LeadStage.CUSTOMER)
INTERESTED_STAGES = (LeadStage.MEETING, LeadStage.PILOT, LeadStage.CUSTOMER)
MEETING_STAGES = (LeadStage.MEETING, LeadStage.PILOT, LeadStage.CUSTOMER)


@dataclass
class FunnelStage:
    key: str
    label: str
    count: int
    note: str
    href: str
    unit: str = "leads"
    conversion: float | None = None  # from the previous stage


@dataclass
class Kpis:
    leads: int = 0
    qualified: int = 0
    emails_sent: int = 0
    emails_mock: int = 0  # of emails_sent: via the MOCK provider (not really sent)
    positive_responses: int = 0
    meetings: int = 0
    invoices_analyzed: int = 0
    money: Summary = field(default_factory=Summary)
    client_payout: Decimal = ZERO
    demo_leads: int = 0
    demo_clients: int = 0

    @property
    def has_demo(self) -> bool:
        return bool(self.demo_leads or self.demo_clients)


def _count(db: Session, stmt) -> int:
    return db.scalar(stmt) or 0


def kpis(db: Session) -> Kpis:
    k = Kpis()
    k.leads = _count(db, select(func.count()).select_from(Prospect))
    k.qualified = _count(db, select(func.count()).select_from(Prospect).where(
        Prospect.qualification.in_((Qualification.STRONG, Qualification.GOOD))))
    k.emails_sent = _count(db, select(func.count()).select_from(OutreachMessage).where(
        OutreachMessage.status == OutreachStatus.SENT))
    k.emails_mock = _count(db, select(func.count()).select_from(OutreachMessage).where(
        OutreachMessage.status == OutreachStatus.SENT, OutreachMessage.delivery == "mock"))
    k.positive_responses = _count(db, select(func.count()).select_from(InboundMessage).where(
        InboundMessage.category.in_(POSITIVE)))
    k.meetings = _count(db, select(func.count()).select_from(Prospect).where(
        Prospect.stage_value.in_(MEETING_STAGES)))
    k.money = summary(db)
    k.invoices_analyzed = k.money.invoices_analyzed
    k.client_payout = k.money.recovered - k.money.success_fee
    k.demo_leads = _count(db, select(func.count()).select_from(Prospect).where(Prospect.is_test_data.is_(True)))
    k.demo_clients = _count(db, select(func.count()).select_from(Client).where(
        Client.company_name.like("[DEMO%")))
    return k


def funnel(db: Session, k: Kpis | None = None) -> list[FunnelStage]:
    k = k or kpis(db)
    contacted_ids = select(OutreachMessage.prospect_id).where(OutreachMessage.status == OutreachStatus.SENT)
    contacted = _count(db, select(func.count()).select_from(Prospect).where(
        Prospect.stage_value.in_(CONTACTED_STAGES) | Prospect.id.in_(contacted_ids)))
    positive_ids = select(InboundMessage.prospect_id).where(InboundMessage.category.in_(POSITIVE))
    interested = _count(db, select(func.count()).select_from(Prospect).where(
        Prospect.stage_value.in_(INTERESTED_STAGES) | Prospect.id.in_(positive_ids)))
    with_docs = _count(db, select(func.count(func.distinct(Document.client_id))))
    with_findings = _count(db, select(func.count(func.distinct(Anomaly.client_id))).where(
        Anomaly.is_stale.is_(False), Anomaly.potential_recovery > 0))
    claims = _count(db, select(func.count()).select_from(RecoveryCase).where(RecoveryCase.submitted_at.is_not(None)))
    recovered = _count(db, select(func.count()).select_from(RecoveryCase).where(RecoveryCase.recovered_amount > 0))
    stages = [
        FunnelStage("leads", "Leads", k.leads, "gevonden of aangemeld", "/app/leads"),
        FunnelStage("qualified", "Gekwalificeerd", k.qualified, "sterk of goed", "/app/leads?qualification=good"),
        FunnelStage("contacted", "Benaderd", contacted, "e-mail verstuurd", "/app/leads?stage=CONTACTED"),
        FunnelStage("interested", "Geïnteresseerd", interested, "positieve reactie of gesprek",
                    "/app/leads?stage=RESPONDED"),
        FunnelStage("invoices", "Facturen ontvangen", with_docs, "klanten met documenten", "/app/invoices", "klanten"),
        FunnelStage("findings", "Afwijkingen gevonden", with_findings, "klanten met mogelijke fouten",
                    "/app/analyses", "klanten"),
        FunnelStage("claims", "Claims ingediend", claims, "dossiers bij leverancier", "/app/cases", "dossiers"),
        FunnelStage("recovered", "Geld teruggevorderd", recovered, "dossiers met ontvangen bedrag",
                    "/app/cases?status=RECOVERED", "dossiers"),
    ]
    for prev, cur in zip(stages, stages[1:], strict=False):
        if prev.count and prev.unit == cur.unit:
            cur.conversion = cur.count / prev.count
    return stages


@dataclass
class ActionItem:
    icon: str
    label: str
    count: int
    href: str
    tone: str  # review | brand | critical


def actions(db: Session, nav_counts: dict | None = None) -> list[ActionItem]:
    approvals = _count(db, select(func.count()).select_from(OutreachMessage).where(
        OutreachMessage.status == OutreachStatus.PENDING_APPROVAL))
    replies = _count(db, select(func.count()).select_from(InboundMessage).where(InboundMessage.handled.is_(False)))
    agent_approvals = _count(db, select(func.count()).select_from(AgentApproval).where(
        AgentApproval.status == ApprovalStatus.PENDING))
    failed = _count(db, select(func.count()).select_from(AgentTask).where(
        AgentTask.status == TaskStatus.FAILED, AgentTask.finished_at >= utcnow() - timedelta(days=7)))
    review = (nav_counts or {}).get("review", 0)
    no_contact = _count(db, select(func.count()).select_from(Prospect).where(
        Prospect.qualification.in_((Qualification.STRONG, Qualification.GOOD)),
        ~Prospect.id.in_(select(Contact.prospect_id)), Prospect.stage_value != LeadStage.REJECTED))
    items = [
        ActionItem("send", "E-mails wachten op goedkeuring", approvals, "/app/outreach?tab=approval", "review"),
        ActionItem("inbox", "Reacties om te lezen", replies, "/app/inbox", "brand"),
        ActionItem("review", "Bevindingen om te beoordelen", review, "/app/review", "review"),
        ActionItem("shield", "Agenttaken wachten op goedkeuring", agent_approvals, "/app/ops", "review"),
        ActionItem("user", "Gekwalificeerd, nog zonder contact", no_contact, "/app/leads?contact=none", "brand"),
        ActionItem("alert", "Mislukte agenttaken (7 dagen)", failed, "/app/ops/tasks?status=FAILED", "critical"),
    ]
    return [i for i in items if i.count]


def leads_per_week(db: Session, weeks: int = 8) -> list[tuple[str, int]]:
    start = utcnow() - timedelta(weeks=weeks)
    rows = db.scalars(select(Prospect.created_at).where(Prospect.created_at >= start)).all()
    buckets = [0] * weeks
    now = utcnow()
    for ts in rows:
        ts = ts if ts.tzinfo else ts.replace(tzinfo=now.tzinfo)
        idx = weeks - 1 - int((now - ts).days // 7)
        if 0 <= idx < weeks:
            buckets[idx] += 1
    labels = [f"wk {(now - timedelta(weeks=weeks - 1 - i)).isocalendar().week}" for i in range(weeks)]
    return list(zip(labels, buckets, strict=True))
