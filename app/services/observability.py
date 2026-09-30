"""Observability for the command center: what the agents are doing, as events plus a snapshot.

One event schema serves both sources the dashboard can show:

- LIVE: derived from the real tables (agent tasks, logs, approvals) plus in-process live steps.
- DEMO: a scripted simulation (app/services/simulation.py), never written to the database.

Every event carries `mode`, so the browser can never mix the two. Nothing here changes agent behaviour; it
only reads.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import live
from app.agents.catalog import AGENTS
from app.agents.topology import EDGES, LANES, PHASES
from app.domain.enums import ApprovalStatus, CaseStatus, Confidence, ReviewStatus, TaskStatus
from app.models import (
    AgentApproval,
    AgentLog,
    AgentRecord,
    AgentTask,
    AnalysisRun,
    Anomaly,
    Client,
    Invoice,
    RecoveryCase,
)
from app.models.base import utcnow
from app.services import agent_ops, metrics
from app.services.recovery import conservative_total

WINDOW = timedelta(minutes=3)  # events are re-sent for this long; the browser de-duplicates by id
LIFECYCLE = {"task.started", "task.completed", "task.failed", "task.paused", "task.cancelled",
             "task.retry_scheduled", "handoff"}
# Too fine-grained for the stream; visible in the task trace (level 3). Approval requests come from their table.
QUIET = {"tool.call", "context.read", "context.write", "approval.requested"}

CASE_AGENTS = ("invoice_intake", "invoice_analysis", "audit", "recovery", "claims")
STAGES = (("received", "Ontvangen"), ("extracted", "Uitgelezen"), ("analyzed", "Geanalyseerd"),
          ("audited", "Gecontroleerd"), ("finding", "Bevinding"), ("validation", "Validatie"),
          ("recovery", "Terugvordering"), ("claim", "Claim"))
EVIDENCE_NODES = (("contract", "Contract"), ("tariff", "Tarief en regels"), ("consumption", "Verbruik"),
                  ("history", "Historische facturen"), ("external", "Externe data"))
CONFIDENCE_NL = {Confidence.HIGH: "Hoog", Confidence.MEDIUM: "Middel", Confidence.LOW: "Laag"}


@dataclass
class Event:
    id: str
    ts: str
    kind: str
    agent: str
    message: str
    mode: str = "live"
    to: str | None = None
    task: dict | None = None
    level: str = "INFO"
    data: dict = field(default_factory=dict)
    at: int | None = None  # demo only: milliseconds after the start of the scenario


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _iso(ts: datetime) -> str:
    return _aware(ts).isoformat(timespec="microseconds")


def _task_ref(t: AgentTask | None) -> dict | None:
    return {"id": t.id, "number": t.number, "title": t.title} if t else None


def _money(v: Decimal | None) -> float:
    return float(v or 0)


# ---------------------------------------------------------------- events (live)


def recent_events(db: Session, window: timedelta = WINDOW, limit: int = 200) -> list[dict]:
    """Everything that happened in the last few minutes, oldest first. Ids are stable across calls."""
    since = utcnow() - window
    out: list[Event] = []
    tasks = {t.id: t for t in db.scalars(select(AgentTask).where(
        (AgentTask.created_at >= since) | (AgentTask.started_at >= since) | (AgentTask.finished_at >= since)
        | AgentTask.status.in_((TaskStatus.RUNNING,)))).all()}
    for t in tasks.values():
        if _aware(t.created_at) >= since and t.created_by_agent_id:
            out.append(Event(f"handoff:{t.id}", _iso(t.created_at), "handoff", t.created_by_agent_id,
                             t.title, to=t.agent_id, task=_task_ref(t)))
        elif _aware(t.created_at) >= since:
            out.append(Event(f"queued:{t.id}", _iso(t.created_at), "task.queued", t.agent_id, t.title,
                             task=_task_ref(t)))
        if t.started_at and _aware(t.started_at) >= since:
            out.append(Event(f"started:{t.id}:{t.attempts}", _iso(t.started_at), "task.started", t.agent_id,
                             t.title, task=_task_ref(t)))
    logs = db.scalars(select(AgentLog).where(AgentLog.created_at >= since).order_by(AgentLog.created_at)
                      .limit(limit * 2)).all()
    for lg in logs:
        if lg.event in QUIET or lg.event in ("task.started", "handoff"):
            continue  # started/handoff come from the task rows (available while the task still runs)
        t = tasks.get(lg.task_id) or (db.get(AgentTask, lg.task_id) if lg.task_id else None)
        kind = lg.event if lg.event in LIFECYCLE else "task.step"
        out.append(Event(_step_id(lg.task_id, lg.created_at, lg.event) if kind == "task.step" else f"log:{lg.id}",
                         _iso(lg.created_at), kind, lg.agent_id, lg.message, task=_task_ref(t), level=lg.level,
                         data=_small(lg.data)))
    for t in tasks.values():  # steps of tasks that are still running in this process
        if t.status != TaskStatus.RUNNING:
            continue
        for e in live.entries(t.id):
            if e.event in QUIET or e.event in ("task.started", "handoff"):
                continue
            out.append(Event(_step_id(t.id, e.created_at, e.event), _iso(e.created_at), "task.step", t.agent_id,
                             e.message, task=_task_ref(t), level=e.level))
        prog = live.progress(t.id)
        if prog:
            out.append(Event(f"progress:{t.id}:{prog[0]}", _iso(utcnow()), "task.progress", t.agent_id,
                             f"{prog[0]} / {prog[1]}", task=_task_ref(t), data={"done": prog[0], "total": prog[1]}))
    for a in db.scalars(select(AgentApproval).where(AgentApproval.requested_at >= since)).all():
        out.append(Event(f"approval:{a.id}", _iso(a.requested_at), "approval.requested", a.agent_id, a.summary,
                         task=_task_ref(tasks.get(a.task_id) or db.get(AgentTask, a.task_id)), level="WARNING"))
    out.sort(key=lambda e: e.ts)
    return [asdict(e) for e in out[-limit:]]


def _step_id(task_id: str | None, ts: datetime, event: str) -> str:
    return f"step:{task_id}:{_iso(ts)}:{event}"


def _small(data: dict | None) -> dict:
    """Only small scalar fields travel to the browser (no payloads, no personal data)."""
    if not isinstance(data, dict):
        return {}
    blocked = {"args", "input", "to_email", "email", "body"}
    return {k: v for k, v in data.items()
            if k not in blocked and (isinstance(v, (int, float, bool)) or (isinstance(v, str) and len(v) < 80))}


# ---------------------------------------------------------------- snapshot (live)


def topology() -> dict:
    names = {a.id: a for a in AGENTS}
    return {
        "lanes": [{"id": lid, "label": label, "agents": list(ids)} for lid, label, ids in LANES],
        "edges": [{"source": e.source, "target": e.target, "kind": e.kind, "label": e.label} for e in EDGES],
        "phases": PHASES,
        "names": {a: names[a].name for a in names},
        "roles": {a: names[a].role for a in names},
        "activity": agent_ops.ACTIVITY_NL,
        "stages": [{"key": k, "label": label} for k, label in STAGES],
        "evidence": [{"key": k, "label": label} for k, label in EVIDENCE_NODES],
    }


def agents_state(db: Session) -> dict[str, dict]:
    d = agent_ops.dashboard(db)
    recent: dict[str, list[dict]] = {}
    for lg in db.scalars(select(AgentLog).where(AgentLog.event.notin_(QUIET))
                         .order_by(AgentLog.created_at.desc()).limit(400)).all():
        items = recent.setdefault(lg.agent_id, [])
        if len(items) < 6:
            items.append({"ts": _iso(lg.created_at), "message": lg.message, "level": lg.level})
    out = {}
    for cards in d["groups"].values():
        for c in cards:
            a = c.agent
            out[a.id] = {
                "state": c.state, "word": c.status_word, "task": _task_ref(c.current_task), "step": c.step,
                "progress": list(c.progress) if c.progress else None, "queued": c.queued,
                "completed": c.completed, "failed": c.failed,
                "last_run": _iso(a.last_run_at) if a.last_run_at else None,
                "last": c.last_activity.message if c.last_activity else None,
                "recent": list(reversed(recent.get(a.id, []))),
            }
    return out


def live_metrics(db: Session) -> dict:
    s = metrics.summary(db)
    analyses = len(db.scalars(select(AgentTask.id).where(
        AgentTask.agent_id.in_(("invoice_intake", "invoice_analysis", "audit")),
        AgentTask.status.in_((TaskStatus.QUEUED, TaskStatus.RUNNING)))).all())
    return {"invoices": s.invoices_analyzed, "analyses": analyses, "potential": _money(s.potential_discrepancies),
            "validated": _money(s.confirmed_discrepancies),
            "cases_open": s.cases_investigating + s.cases_submitted, "cases_recovered": s.cases_recovered}


def live_case(db: Session) -> dict | None:
    """The most recent invoice-recovery run, as the Live Case panel shows it.

    Normally the run starts with an Invoice Intake task. Data that was analysed without the agents (e.g. seeded
    or analysed from the client page) is shown from its latest analysis run, with the stages the data proves.
    """
    start = db.scalars(select(AgentTask).where(AgentTask.agent_id == "invoice_intake", AgentTask.client_id.isnot(None))
                       .order_by(AgentTask.created_at.desc()).limit(1)).first()
    last_run = db.scalars(select(AnalysisRun).order_by(AnalysisRun.created_at.desc()).limit(1)).first()
    if start is None and last_run is None:
        return None
    use_task = start is not None and (last_run is None or _aware(start.created_at) >= _aware(last_run.created_at)
                                      - timedelta(minutes=5))
    client = db.get(Client, start.client_id if use_task else last_run.client_id)
    if client is None:
        return None
    since = start.created_at if use_task else last_run.created_at
    tasks = db.scalars(select(AgentTask).where(AgentTask.client_id == client.id, AgentTask.created_at >= since)
                       .order_by(AgentTask.created_at)).all()
    has_run = db.scalar(select(AnalysisRun.id).where(AnalysisRun.client_id == client.id).limit(1)) is not None
    latest = {}
    for t in tasks:
        latest[t.agent_id] = t
    invoice = db.scalars(select(Invoice).where(Invoice.client_id == client.id)
                         .order_by(Invoice.created_at.desc()).limit(1)).first()
    anomalies = db.scalars(select(Anomaly).where(Anomaly.client_id == client.id, Anomaly.is_stale.is_(False),
                                                 Anomaly.review_status.notin_((ReviewStatus.REJECTED,
                                                                               ReviewStatus.DUPLICATE)))).all()
    confirmed = [a for a in anomalies if a.review_status == ReviewStatus.CONFIRMED]
    cases = db.scalars(select(RecoveryCase).where(RecoveryCase.client_id == client.id)
                       .order_by(RecoveryCase.created_at.desc())).all()

    def done(agent_id):
        t = latest.get(agent_id)
        return t is not None and t.status == TaskStatus.COMPLETED

    claim_statuses = {CaseStatus.CLAIM_PREPARED, CaseStatus.CLIENT_APPROVAL, CaseStatus.SUBMITTED,
                      CaseStatus.SUPPLIER_REVIEW, CaseStatus.NEGOTIATION, CaseStatus.APPROVED, CaseStatus.RECOVERED,
                      CaseStatus.CLOSED}
    reached = {
        "received": True, "extracted": done("invoice_intake") or invoice is not None,
        "analyzed": done("invoice_analysis") or (has_run and "invoice_analysis" not in latest),
        "audited": done("audit"), "finding": bool(anomalies), "validation": bool(confirmed),
        "recovery": bool(cases), "claim": any(c.status in claim_statuses for c in cases),
    }
    stages, current = [], None
    for key, label in STAGES:
        state = "done" if reached[key] else ("active" if current is None else "pending")
        if state == "active":
            current = key
        stages.append({"key": key, "label": label, "state": state})
    agents = []
    for agent_id in CASE_AGENTS:
        t = latest.get(agent_id)
        state = {TaskStatus.COMPLETED: "done", TaskStatus.RUNNING: "active", TaskStatus.QUEUED: "queued",
                 TaskStatus.WAITING_APPROVAL: "waiting", TaskStatus.FAILED: "failed",
                 TaskStatus.CANCELLED: "failed"}.get(t.status, "pending") if t else "pending"
        note = None
        if t is not None:
            note = (t.output or {}).get("summary") if t.status == TaskStatus.COMPLETED and isinstance(t.output, dict) \
                else (agent_ops.live_step(t.id) or t.title if t.status == TaskStatus.RUNNING else t.error or t.title)
        agents.append({"agent": agent_id, "state": state, "note": note})
    # Status: what is happening now, or who the case is waiting for (never claims work that is not running).
    stage_agent = {"received": ("invoice_intake", "Ontvangen"), "extracted": ("invoice_intake", "Uitlezen"),
                   "analyzed": ("invoice_analysis", "Analyseren"), "finding": ("invoice_analysis", "Analyseren"),
                   "audited": ("audit", "Controleren"), "recovery": ("recovery", "Dossier voorbereiden"),
                   "claim": ("claims", "Claim opstellen")}
    if current is None:
        status = "Klaar voor indiening"
    elif current == "validation":
        status = "Wacht op specialist"
    elif current == "finding" and done("invoice_analysis"):
        status = "Geen bevindingen"
    else:
        agent_id, verb = stage_agent.get(current, (None, "Bezig"))
        t = latest.get(agent_id)
        status = verb if t is not None and t.status in (TaskStatus.RUNNING, TaskStatus.QUEUED) \
            else f"Wacht op {next((a.name for a in AGENTS if a.id == agent_id), 'de volgende stap')}"
    top = max(anomalies, key=lambda a: (a.review_status == ReviewStatus.CONFIRMED, a.potential_recovery or 0),
              default=None)
    return {
        "client": client.company_name, "synthetic": client.company_name.startswith("[DEMO"),
        "invoice": {"number": invoice.invoice_number if invoice else None,
                    "supplier": invoice.supplier if invoice else None,
                    "amount": _money(invoice.total_incl_vat) if invoice and invoice.total_incl_vat else None},
        "status": status, "current": current, "stages": stages, "agents": agents,
        "opportunity": {"potential": _money(conservative_total(anomalies)),
                        "validated": _money(conservative_total(confirmed)),
                        "findings": len(anomalies), "confirmed": len(confirmed)},
        "evidence": evidence_view(top),
        "href": f"/app/clients/{client.id}",
    }


def evidence_view(a: Anomaly | None) -> dict | None:
    """Group a finding's source references into the evidence nodes of the command center."""
    if a is None:
        return None
    nodes: dict[str, list[str]] = {k: [] for k, _ in EVIDENCE_NODES}
    for ev in a.evidence or []:
        kind = ev.get("kind")
        if kind == "contract_price":
            nodes["contract"].append(ev.get("label", "Contract"))
        elif kind == "meter_reading":
            nodes["consumption"].append(ev.get("label", "Meterstand"))
        elif kind == "reference_rate":
            nodes["external"].append(ev.get("label", "Referentietarief"))
        elif kind == "invoice_field" and a.invoice_id and ev.get("invoice_id") not in (None, a.invoice_id):
            nodes["history"].append(ev.get("label", "Andere factuur"))
        else:
            nodes["tariff"].append(ev.get("label", "Factuurregel"))
    return {
        "title": a.title, "amount": _money(a.potential_recovery),
        "confidence": CONFIDENCE_NL.get(a.confidence, str(a.confidence)),
        "validated": a.review_status == ReviewStatus.CONFIRMED,
        "sources": sum(len(v) for v in nodes.values()),
        "nodes": [{"key": k, "label": label, "items": nodes[k][:4], "count": len(nodes[k])}
                  for k, label in EVIDENCE_NODES],
        "href": f"/app/anomalies/{a.id}",
    }


def snapshot(db: Session) -> dict:
    pending = db.scalars(select(AgentApproval).where(AgentApproval.status == ApprovalStatus.PENDING)).all()
    records = {r.id: r for r in db.scalars(select(AgentRecord)).all()}
    return {
        "mode": "live", "generated_at": _iso(utcnow()), "metrics": live_metrics(db), "agents": agents_state(db),
        "case": live_case(db), "approvals": len(pending),
        "operational": sum(1 for r in records.values() if r.implemented and r.enabled), "total": len(records),
    }
