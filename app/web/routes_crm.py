"""Sales: dashboard, leads (CRM), lead research, companies, contacts, outreach with approval, inbox."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.agents.catalog import AGENTS
from app.agents.lead_researcher import COUNTRY_AREAS, MAX_LIMIT, PRESETS, SECTORS
from app.agents.queue import enqueue
from app.agents.runner import kick
from app.config import get_settings
from app.db import get_db
from app.domain.enums import (
    Confidence,
    LeadStage,
    OutreachKind,
    OutreachStatus,
    Qualification,
    ReplyCategory,
    TaskStatus,
)
from app.integrations.email.provider import MockEmailProvider, get_email_provider, provider_status
from app.integrations.web.contacts import ROLE_LABELS, role_category
from app.integrations.web.research import get_research_provider
from app.models import (
    AgentTask,
    Client,
    Contact,
    InboundMessage,
    LeadTask,
    OutreachMessage,
    Prospect,
    ProspectEvent,
    User,
)
from app.services import business, crm, outreach_copy
from app.services.agent_ops import pulse
from app.services.audit import audit
from app.web.deps import _nav_counts, client_ip, flash, redirect, render
from app.web.security import require_staff, verify_csrf

router = APIRouter()
PAGE_SIZE = 50


def _schedule(background: BackgroundTasks) -> None:
    if get_settings().agents_autorun:
        background.add_task(kick)


def _prospect(db: Session, prospect_id: str) -> Prospect:
    p = db.get(Prospect, prospect_id)
    if p is None:
        raise HTTPException(status_code=404)
    return p


# ---------------------------------------------------------------- dashboard


@router.get("/app/dashboard")
def dashboard(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    crm.sync_inbound_leads(db)
    db.commit()
    k = business.kpis(db)
    nav = _nav_counts()
    recent = db.scalars(select(Prospect).options(selectinload(Prospect.contacts))
                        .order_by(Prospect.updated_at.desc()).limit(6)).all()
    tasks = db.scalars(select(AgentTask).order_by(AgentTask.created_at.desc()).limit(5)).all()
    return render(request, "crm/dashboard.html", user=user, k=k, funnel=business.funnel(db, k),
                  actions=business.actions(db, nav), nav=nav, recent=recent, tasks=tasks,
                  weekly=business.leads_per_week(db), now=datetime.now(),
                  agent_names={a.id: a.name for a in AGENTS})


# ---------------------------------------------------------------- leads


def _lead_query(q: str, sector: str, stage: str, qualification: str, city: str, contact: str):
    stmt = select(Prospect)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Prospect.company_name).like(like), func.lower(Prospect.city).like(like),
                              func.lower(Prospect.domain).like(like)))
    if sector:
        stmt = stmt.where(Prospect.sector == sector)
    if stage in LeadStage.__members__:
        stmt = stmt.where(Prospect.stage_value == LeadStage(stage))
    if qualification == "good":
        stmt = stmt.where(Prospect.qualification.in_((Qualification.STRONG, Qualification.GOOD)))
    elif qualification == "none":
        stmt = stmt.where(Prospect.qualification.is_(None))
    elif qualification in Qualification.__members__:
        stmt = stmt.where(Prospect.qualification == Qualification(qualification))
    if city:
        stmt = stmt.where(func.lower(Prospect.city).like(f"%{city.lower()}%"))
    has_contact = select(Contact.prospect_id)
    if contact == "none":
        stmt = stmt.where(~Prospect.id.in_(has_contact))
    elif contact == "named":
        stmt = stmt.where(Prospect.id.in_(select(Contact.prospect_id).where(Contact.full_name.is_not(None))))
    elif contact == "email":
        stmt = stmt.where(Prospect.id.in_(select(Contact.prospect_id).where(Contact.email.is_not(None))))
    elif contact == "contacted":
        stmt = stmt.where(Prospect.id.in_(select(OutreachMessage.prospect_id).where(
            OutreachMessage.status == OutreachStatus.SENT)))
    return stmt


@router.get("/app/leads")
def leads(request: Request, q: str = "", sector: str = "", stage: str = "", qualification: str = "", city: str = "",
          contact: str = "", page: int = 1, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    if crm.sync_inbound_leads(db):
        db.commit()
    base = _lead_query(q.strip(), sector, "", qualification, city.strip(), contact)
    stage_counts = _stage_counts(db, base)
    stmt = _lead_query(q.strip(), sector, stage, qualification, city.strip(), contact)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    rows = db.scalars(stmt.options(selectinload(Prospect.contacts))
                      .order_by(Prospect.fit_score.desc(), Prospect.created_at.desc())
                      .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
    sectors_used = [s for s in db.scalars(select(Prospect.sector).distinct()).all() if s]
    filters = {"q": q, "sector": sector, "stage": stage, "qualification": qualification, "city": city,
               "contact": contact}
    from urllib.parse import urlencode

    qs = urlencode({k: v for k, v in filters.items() if v})
    qs_nostage = urlencode({k: v for k, v in filters.items() if v and k != "stage"})
    return render(request, "crm/leads.html", user=user, leads=rows, total=total, page=page, qs=qs,
                  qs_nostage=qs_nostage,
                  pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE), filters=filters, stage_counts=stage_counts,
                  all_count=sum(stage_counts.values()), sectors_used=sectors_used,
                  any_leads=db.scalar(select(func.count()).select_from(Prospect)) or 0)


def _stage_counts(db: Session, base) -> dict:
    sub = base.subquery()
    rows = db.execute(select(sub.c.stage, func.count()).group_by(sub.c.stage)).all()
    out = {}
    for stage, n in rows:
        key = LeadStage(stage) if stage else LeadStage.NEW
        out[key] = out.get(key, 0) + n
    return out


@router.post("/app/leads/bulk", dependencies=[Depends(verify_csrf)])
async def leads_bulk(request: Request, background: BackgroundTasks, user: User = Depends(require_staff),
                     db: Session = Depends(get_db)):
    form = await request.form()
    ids = [str(i) for i in form.getlist("ids")][:200]
    action = str(form.get("action", ""))
    back = str(form.get("next_url", "/app/leads"))
    back = back if back.startswith("/app/") else "/app/leads"
    if not ids:
        flash(request, "Selecteer eerst één of meer leads.", "error")
        return redirect(back)
    jobs = {"qualify": ("lead_qualifier", "qualify_prospects", "Kwalificeer {n} leads"),
            "contacts": ("contact_researcher", "find_contacts", "Zoek beslissers bij {n} bedrijven"),
            "draft": ("outreach", "draft_outreach", "Stel e-mails op voor {n} bedrijven")}
    if action not in jobs:
        flash(request, "Onbekende actie.", "error")
        return redirect(back)
    agent, task_type, title = jobs[action]
    task = enqueue(db, agent, task_type, {"prospect_ids": ids, "continue": False}, title=title.format(n=len(ids)),
                   created_by_user_id=user.id, priority=6, workflow_id="lead_generation")
    audit(db, "agent.task_created", user=user, object_type="agent_task", object_id=task.id, ip=client_ip(request),
          details={"agent": agent, "count": len(ids)})
    db.commit()
    _schedule(background)
    flash(request, f"Taak #{task.number} gestart voor {len(ids)} leads.", "success")
    return redirect(f"/app/ops/tasks/{task.id}")


@router.get("/app/leads/find")
def leads_find(request: Request, user: User = Depends(require_staff)):
    provider = get_research_provider()
    s = get_settings()
    return render(request, "crm/find.html", user=user, sectors=SECTORS.values(), provider=provider,
                  presets=[(k, lbl.split(' (')[0], ', '.join(SECTORS[x].label for x in keys))
                           for k, (lbl, keys) in PRESETS.items()],
                  max_limit=MAX_LIMIT, threshold=s.research_approval_threshold, qualify=s.lead_qualify_threshold)


@router.post("/app/leads/find", dependencies=[Depends(verify_csrf)])
async def leads_find_start(request: Request, background: BackgroundTasks, user: User = Depends(require_staff),
                           db: Session = Depends(get_db)):
    form = await request.form()
    preset = str(form.get("preset", ""))
    sectors = [s for s in form.getlist("sectors") if s in SECTORS]
    if preset in PRESETS:
        sectors += [s for s in PRESETS[preset][1] if s not in sectors]
    country = str(form.get("country", "NL"))
    area = str(form.get("area", "")).strip()[:80] or COUNTRY_AREAS.get(country, "")
    target = str(form.get("target", "25")).strip()
    min_locations = str(form.get("min_locations", "1")).strip()
    criteria = str(form.get("criteria", "")).strip()[:300]
    errors = []
    if not sectors:
        errors.append("Kies een branchegroep of ten minste één branche.")
    if not area:
        errors.append("Kies een land of vul een regio in.")
    if not target.isdigit() or not 1 <= int(target) <= MAX_LIMIT:
        errors.append(f"Kies een aantal tussen 1 en {MAX_LIMIT}.")
    if not min_locations.isdigit():
        min_locations = "1"
    if errors:
        for e in errors:
            flash(request, e, "error")
        return redirect("/app/leads/find")
    full = form.get("workflow") == "on"
    what = PRESETS[preset][0].split(" (")[0].lower() if preset in PRESETS else \
        ", ".join(SECTORS[s].label.lower() for s in sectors)
    task = enqueue(db, "lead_researcher", "research_prospects",
                   {"sectors": sectors, "area": area, "country": country, "target": int(target),
                    "min_locations": int(min_locations), "criteria": criteria,
                    "require_website": form.get("require_website") == "on"},
                   title=f"Vind {target} bedrijven: {what} in {area}",
                   workflow_id="lead_generation" if full else None, created_by_user_id=user.id, priority=6)
    audit(db, "agent.task_created", user=user, object_type="agent_task", object_id=task.id, ip=client_ip(request),
          details={"agent": "lead_researcher"})
    db.commit()
    _schedule(background)
    flash(request, f"Onderzoek #{task.number} gestart. U ziet de voortgang hieronder live.", "success")
    return redirect(f"/app/ops/tasks/{task.id}")


@router.get("/app/leads/{prospect_id}")
def lead_detail(prospect_id: str, request: Request, user: User = Depends(require_staff),
                db: Session = Depends(get_db)):
    p = _prospect(db, prospect_id)
    emails = db.scalars(select(OutreachMessage).where(OutreachMessage.prospect_id == p.id)
                          .order_by(OutreachMessage.created_at.desc())).all()
    replies = db.scalars(select(InboundMessage).where(InboundMessage.prospect_id == p.id)
                         .order_by(InboundMessage.received_at.desc())).all()
    events = db.scalars(select(ProspectEvent).where(ProspectEvent.prospect_id == p.id)
                        .order_by(ProspectEvent.created_at.desc()).limit(40)).all()
    open_tasks = db.scalars(select(AgentTask).where(
        AgentTask.status.in_((TaskStatus.QUEUED, TaskStatus.RUNNING)),
        AgentTask.agent_id.in_(("lead_qualifier", "contact_researcher", "outreach")))).all()
    running = [t for t in open_tasks if p.id in (t.input or {}).get("prospect_ids", [])]
    users = {u.id: u for u in db.scalars(select(User)).all()}
    tasks = db.scalars(select(LeadTask).where(LeadTask.prospect_id == p.id)
                       .order_by(LeadTask.done_at.is_not(None), LeadTask.due_at)).all()
    return render(request, "crm/lead.html", user=user, p=p, sector=SECTORS.get(p.sector), emails=emails,
                  replies=replies, events=events, running=running, users=users, stages=list(LeadStage),
                  tasks=tasks, agent_names={a.id: a.name for a in AGENTS}, pulse=pulse(db) if running else None,
                  role_labels=ROLE_LABELS, client=db.get(Client, p.client_id) if p.client_id else None,
                  qualify=get_settings().lead_qualify_threshold, mail=provider_status())


def _lead_action(db: Session, p: Prospect, agent: str, task_type: str, title: str, user: User,
                 extra: dict | None = None) -> AgentTask:
    return enqueue(db, agent, task_type, {"prospect_ids": [p.id], "continue": False, **(extra or {})}, title=title,
                   created_by_user_id=user.id, priority=7, workflow_id="lead_generation")


@router.post("/app/leads/{prospect_id}/tasks/{task_id}", dependencies=[Depends(verify_csrf)])
def lead_task_done(prospect_id: str, task_id: str, request: Request, user: User = Depends(require_staff),
                   db: Session = Depends(get_db)):
    t = db.get(LeadTask, task_id)
    if t is None or t.prospect_id != prospect_id:
        raise HTTPException(status_code=404)
    if t.done_at is None:
        from app.models.base import utcnow

        t.done_at = utcnow()
        crm.add_event(db, prospect_id, "task", f"Taak afgerond: {t.title}", user=user)
        db.commit()
    return redirect(f"/app/leads/{prospect_id}")


@router.post("/app/leads/{prospect_id}/find-contacts", dependencies=[Depends(verify_csrf)])
def lead_find_contacts(prospect_id: str, request: Request, background: BackgroundTasks,
                       user: User = Depends(require_staff), db: Session = Depends(get_db)):
    p = _prospect(db, prospect_id)
    if not p.website:
        flash(request, "Er is geen website bekend; voeg een contact handmatig toe.", "error")
        return redirect(f"/app/leads/{p.id}")
    task = _lead_action(db, p, "contact_researcher", "find_contacts", f"Zoek beslisser bij {p.company_name}", user)
    db.commit()
    _schedule(background)
    flash(request, "De Contact Researcher leest de openbare website. Resultaten verschijnen hieronder.", "success")
    return redirect(f"/app/leads/{p.id}?task={task.id}")


@router.post("/app/leads/{prospect_id}/draft", dependencies=[Depends(verify_csrf)])
def lead_draft(prospect_id: str, request: Request, background: BackgroundTasks, contact_id: str = Form(""),
               user: User = Depends(require_staff), db: Session = Depends(get_db)):
    p = _prospect(db, prospect_id)
    if not any(c.email and not c.do_not_contact for c in p.contacts):
        flash(request, "Er is nog geen contact met e-mailadres. Zoek eerst een beslisser.", "error")
        return redirect(f"/app/leads/{p.id}")
    _lead_action(db, p, "outreach", "draft_outreach", f"E-mail opstellen voor {p.company_name}", user,
                 {"contact_id": contact_id} if contact_id else None)
    db.commit()
    _schedule(background)
    flash(request, "Concept-e-mail wordt opgesteld; u vindt hem zo onder Outreach om te beoordelen.", "success")
    return redirect(f"/app/leads/{p.id}")


@router.post("/app/leads/{prospect_id}/qualify", dependencies=[Depends(verify_csrf)])
def lead_qualify(prospect_id: str, request: Request, background: BackgroundTasks,
                 user: User = Depends(require_staff), db: Session = Depends(get_db)):
    p = _prospect(db, prospect_id)
    _lead_action(db, p, "lead_qualifier", "qualify_prospects", f"Kwalificeer {p.company_name}", user)
    db.commit()
    _schedule(background)
    flash(request, "Kwalificatie gestart.", "success")
    return redirect(f"/app/leads/{p.id}")


@router.post("/app/leads/{prospect_id}/update", dependencies=[Depends(verify_csrf)])
def lead_update(prospect_id: str, request: Request, stage: str = Form(""), next_action: str = Form(""),
                next_action_at: str = Form(""), notes: str = Form(""), user: User = Depends(require_staff),
                db: Session = Depends(get_db)):
    p = _prospect(db, prospect_id)
    if stage in LeadStage.__members__:
        crm.set_stage(db, p, LeadStage(stage), reason="handmatig", user=user)
    p.next_action = next_action.strip()[:300] or None
    try:
        p.next_action_at = datetime.fromisoformat(next_action_at) if next_action_at else None
    except ValueError:
        flash(request, "Ongeldige datum voor de volgende actie.", "error")
    if notes.strip() != (p.notes or ""):
        p.notes = notes.strip() or None
        crm.add_event(db, p.id, "note", "Notitie bijgewerkt", user=user)
    audit(db, "lead.updated", user=user, object_type="prospect", object_id=p.id, details={"stage": p.stage.value})
    db.commit()
    flash(request, "Lead bijgewerkt.", "success")
    return redirect(f"/app/leads/{p.id}")


@router.post("/app/leads/{prospect_id}/contacts", dependencies=[Depends(verify_csrf)])
def lead_add_contact(prospect_id: str, request: Request, full_name: str = Form(""), role: str = Form(""),
                     email: str = Form(""), linkedin_url: str = Form(""), phone: str = Form(""),
                     source_url: str = Form(""), user: User = Depends(require_staff), db: Session = Depends(get_db)):
    p = _prospect(db, prospect_id)
    email, linkedin_url, source_url = email.strip().lower(), linkedin_url.strip(), source_url.strip()
    if not (full_name.strip() or email):
        flash(request, "Vul ten minste een naam of e-mailadres in.", "error")
        return redirect(f"/app/leads/{p.id}")
    if email and ("@" not in email or " " in email):
        flash(request, "Dit e-mailadres is ongeldig.", "error")
        return redirect(f"/app/leads/{p.id}")
    if linkedin_url and not linkedin_url.startswith("https://www.linkedin.com/"):
        flash(request, "Gebruik een volledige LinkedIn-link (https://www.linkedin.com/…).", "error")
        return redirect(f"/app/leads/{p.id}")
    if not source_url:
        flash(request, "Vermeld waar u dit contact gevonden heeft (bron-URL of omschrijving).", "error")
        return redirect(f"/app/leads/{p.id}")
    c = crm.save_contact(db, p, full_name=full_name.strip()[:200] or None, role=role.strip()[:200] or None,
                         role_category=role_category(role), email=email or None,
                         email_type=_email_type(email), linkedin_url=linkedin_url or None, source="manual",
                         source_url=source_url[:500], excerpt=f"Handmatig vastgelegd door {user.email}",
                         confidence=Confidence.HIGH, user=user)
    if c is None:
        flash(request, "Dit contact bestaat al bij deze lead.", "info")
    else:
        c.phone = phone.strip()[:50] or None
        audit(db, "contact.created", user=user, object_type="contact", object_id=c.id)
        flash(request, "Contact toegevoegd.", "success")
    db.commit()
    return redirect(f"/app/leads/{p.id}#contacten")


def _email_type(email: str) -> str | None:
    from app.integrations.web.contacts import GENERIC_LOCAL

    if not email:
        return None
    return "GENERAL_COMPANY_EMAIL" if email.split("@")[0] in GENERIC_LOCAL else "PERSONAL_BUSINESS"


@router.post("/app/leads/{prospect_id}/do-not-contact", dependencies=[Depends(verify_csrf)])
def lead_dnc(prospect_id: str, request: Request, reason: str = Form(""), contact_id: str = Form(""),
             user: User = Depends(require_staff), db: Session = Depends(get_db)):
    p = _prospect(db, prospect_id)
    contact = db.get(Contact, contact_id) if contact_id else None
    if contact is not None and contact.prospect_id != p.id:
        raise HTTPException(status_code=404)
    crm.mark_do_not_contact(db, p, contact, reason.strip() or "op verzoek", user=user)
    if contact is None:
        crm.set_stage(db, p, LeadStage.REJECTED, reason="niet benaderen", user=user)
    audit(db, "lead.do_not_contact", user=user, object_type="prospect", object_id=p.id)
    db.commit()
    flash(request, "Vastgelegd: niet meer benaderen. Openstaande concepten zijn ingetrokken.", "success")
    return redirect(f"/app/leads/{p.id}")


# ---------------------------------------------------------------- companies & contacts


@router.get("/app/companies")
def companies(request: Request, tab: str = "prospects", q: str = "", sector: str = "",
              user: User = Depends(require_staff), db: Session = Depends(get_db)):
    from app.services.metrics import summary

    stmt = select(Prospect).options(selectinload(Prospect.contacts))
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Prospect.company_name).like(like), func.lower(Prospect.city).like(like)))
    if sector:
        stmt = stmt.where(Prospect.sector == sector)
    rows = db.scalars(stmt.order_by(Prospect.company_name).limit(500)).all()
    clients = db.scalars(select(Client).order_by(Client.company_name)).all()
    by_sector = dict(db.execute(select(Prospect.sector, func.count()).group_by(Prospect.sector)).all())
    return render(request, "crm/companies.html", user=user, tab=tab if tab in ("prospects", "clients") else
                  "prospects", companies=rows, clients=clients, by_sector=by_sector, q=q, sector=sector,
                  summaries={c.id: summary(db, c.id) for c in clients} if tab == "clients" else {},
                  total=db.scalar(select(func.count()).select_from(Prospect)) or 0)


@router.get("/app/contacts")
def contacts(request: Request, q: str = "", role: str = "", confidence: str = "", email: str = "",
             user: User = Depends(require_staff), db: Session = Depends(get_db)):
    stmt = select(Contact).join(Prospect).options(selectinload(Contact.prospect))
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Contact.full_name).like(like), func.lower(Contact.email).like(like),
                              func.lower(Prospect.company_name).like(like)))
    if role:
        stmt = stmt.where(Contact.role_category == role)
    if confidence in Confidence.__members__:
        stmt = stmt.where(Contact.confidence == Confidence(confidence))
    if email == "yes":
        stmt = stmt.where(Contact.email.is_not(None))
    elif email == "named":
        stmt = stmt.where(Contact.full_name.is_not(None))
    rows = db.scalars(stmt.order_by(Contact.created_at.desc()).limit(500)).all()
    sent_to = set(db.scalars(select(OutreachMessage.contact_id).where(OutreachMessage.status ==
                                                                      OutreachStatus.SENT)).all())
    return render(request, "crm/contacts.html", user=user, contacts=rows, sent_to=sent_to,
                  filters={"q": q, "role": role, "confidence": confidence, "email": email},
                  total=db.scalar(select(func.count()).select_from(Contact)) or 0)


# ---------------------------------------------------------------- outreach


OUTREACH_TABS = {
    "approval": ("Wacht op goedkeuring", (OutreachStatus.PENDING_APPROVAL,)),
    "approved": ("Goedgekeurd", (OutreachStatus.APPROVED, OutreachStatus.SCHEDULED)),
    "sent": ("Verstuurd", (OutreachStatus.SENT, OutreachStatus.OUTBOX)),
    "failed": ("Mislukt", (OutreachStatus.FAILED,)),
    "rejected": ("Afgewezen", (OutreachStatus.REJECTED,)),
}


@router.get("/app/outreach")
def outreach(request: Request, tab: str = "approval", user: User = Depends(require_staff),
             db: Session = Depends(get_db)):
    counts = {k: db.scalar(select(func.count()).select_from(OutreachMessage).where(
        OutreachMessage.status.in_(st))) or 0 for k, (_, st) in OUTREACH_TABS.items()}
    replies = db.scalar(select(func.count()).select_from(InboundMessage)) or 0
    followups_due = db.scalar(select(func.count()).select_from(Prospect).where(
        Prospect.next_action_at.is_not(None), Prospect.next_action_at <= datetime.now(),
        Prospect.do_not_contact.is_not(True))) or 0
    items = []
    if tab in OUTREACH_TABS:
        items = db.scalars(select(OutreachMessage).where(OutreachMessage.status.in_(OUTREACH_TABS[tab][1]))
                              .options(selectinload(OutreachMessage.prospect), selectinload(OutreachMessage.contact))
                              .order_by(OutreachMessage.created_at.desc()).limit(200)).all()
    followups = []
    if tab == "followups":
        followups = db.scalars(select(OutreachMessage).where(OutreachMessage.kind == OutreachKind.FOLLOW_UP)
                               .options(selectinload(OutreachMessage.prospect))
                               .order_by(OutreachMessage.created_at.desc()).limit(200)).all()
    inbound = []
    if tab == "responses":
        inbound = db.scalars(select(InboundMessage).options(selectinload(InboundMessage.prospect))
                             .order_by(InboundMessage.received_at.desc()).limit(200)).all()
    return render(request, "crm/outreach.html", user=user, tab=tab, tabs=OUTREACH_TABS, counts=counts,
                  items=items, followups=followups, inbound=inbound, replies=replies,
                  followups_due=followups_due, mail=provider_status(), follow_days=get_settings().follow_up_days)


def _message(db: Session, message_id: str) -> OutreachMessage:
    msg = db.get(OutreachMessage, message_id)
    if msg is None:
        raise HTTPException(status_code=404)
    return msg


@router.get("/app/outreach/{message_id}")
def outreach_message(message_id: str, request: Request, edit: int = 0, user: User = Depends(require_staff),
                     db: Session = Depends(get_db)):
    msg = _message(db, message_id)
    queue = db.scalars(select(OutreachMessage.id).where(OutreachMessage.status == OutreachStatus.PENDING_APPROVAL)
                       .order_by(OutreachMessage.created_at.desc())).all()
    nxt = next((m for m in queue if m != msg.id), None)
    reply_to = db.get(InboundMessage, msg.in_reply_to_id) if msg.in_reply_to_id else None
    users = {u.id: u for u in db.scalars(select(User)).all()}
    task = db.scalar(select(AgentTask).where(AgentTask.task_type == "send_email",
                                             AgentTask.input["message_id"].as_string() == msg.id)
                     .order_by(AgentTask.created_at.desc()))
    return render(request, "crm/message.html", user=user, m=msg, p=msg.prospect, c=msg.contact, edit=bool(edit),
                  next_id=nxt, position=(queue.index(msg.id) + 1) if msg.id in queue else None, queue_len=len(queue),
                  reply_to=reply_to, users=users, mail=provider_status(), task=task,
                  words=outreach_copy.word_count(outreach_copy.core_text(msg.body)),
                  pulse=pulse(db) if msg.status == OutreachStatus.APPROVED else None)


@router.post("/app/outreach/{message_id}/save", dependencies=[Depends(verify_csrf)])
def outreach_save(message_id: str, request: Request, subject: str = Form(...), body: str = Form(...),
                  user: User = Depends(require_staff), db: Session = Depends(get_db)):
    msg = _message(db, message_id)
    try:
        crm.update_message(db, msg, user, subject, body)
        db.commit()
        flash(request, "Wijzigingen opgeslagen.", "success")
    except crm.CrmError as exc:
        flash(request, str(exc), "error")
    return redirect(f"/app/outreach/{msg.id}")


@router.post("/app/outreach/{message_id}/approve", dependencies=[Depends(verify_csrf)])
def outreach_approve(message_id: str, request: Request, background: BackgroundTasks, subject: str = Form(None),
                     body: str = Form(None), next_id: str = Form(""), user: User = Depends(require_staff),
                     db: Session = Depends(get_db)):
    msg = _message(db, message_id)
    try:
        task = crm.approve_and_send(db, msg, user, subject=subject, body=body)
        audit(db, "outreach.approved", user=user, object_type="outreach_message", object_id=msg.id,
              ip=client_ip(request), details={"task": task.id})
        db.commit()
    except crm.CrmError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return redirect(f"/app/outreach/{msg.id}")
    _schedule(background)
    live = get_email_provider().is_live
    flash(request, "Goedgekeurd. De Email-agent verstuurt het bericht." if live else
          "Goedgekeurd. MOCK-modus: het bericht wordt niet echt verzonden, maar als .eml opgeslagen.", "success")
    return redirect(f"/app/outreach/{next_id}" if next_id else "/app/outreach?tab=approval")


@router.post("/app/outreach/{message_id}/reject", dependencies=[Depends(verify_csrf)])
def outreach_reject(message_id: str, request: Request, note: str = Form(""), next_id: str = Form(""),
                    user: User = Depends(require_staff), db: Session = Depends(get_db)):
    msg = _message(db, message_id)
    try:
        crm.reject_message(db, msg, user, note)
        audit(db, "outreach.rejected", user=user, object_type="outreach_message", object_id=msg.id)
        db.commit()
        flash(request, "Concept afgewezen.", "success")
    except crm.CrmError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/app/outreach/{msg.id}")
    return redirect(f"/app/outreach/{next_id}" if next_id else "/app/outreach?tab=approval")


@router.post("/app/outreach/{message_id}/reopen", dependencies=[Depends(verify_csrf)])
def outreach_reopen(message_id: str, request: Request, user: User = Depends(require_staff),
                    db: Session = Depends(get_db)):
    """A failed or locally stored message goes back to the approval queue (e.g. after configuring SMTP)."""
    msg = _message(db, message_id)
    if msg.status not in (OutreachStatus.FAILED, OutreachStatus.OUTBOX, OutreachStatus.REJECTED):
        flash(request, "Alleen mislukte of afgewezen berichten kunnen opnieuw worden aangeboden.", "error")
        return redirect(f"/app/outreach/{msg.id}")
    p = db.get(Prospect, msg.prospect_id)
    blocked = crm.suppression_reason(db, p, msg.contact, msg.to_email)
    if blocked:
        flash(request, f"Niet benaderen: {blocked}.", "error")
        return redirect(f"/app/outreach/{msg.id}")
    msg.status, msg.error = OutreachStatus.PENDING_APPROVAL, None
    crm.add_event(db, msg.prospect_id, "outreach", "Opnieuw ter goedkeuring aangeboden", user=user,
                  data={"message_id": msg.id})
    db.commit()
    flash(request, "Het bericht staat weer klaar voor goedkeuring.", "success")
    return redirect(f"/app/outreach/{msg.id}")


@router.post("/app/outreach/follow-ups", dependencies=[Depends(verify_csrf)])
def outreach_followups(request: Request, background: BackgroundTasks, user: User = Depends(require_staff),
                       db: Session = Depends(get_db)):
    task = enqueue(db, "follow_up", "schedule_follow_up", {}, title="Controleer onbeantwoorde e-mails",
                   created_by_user_id=user.id, priority=5)
    db.commit()
    _schedule(background)
    flash(request, f"Taak #{task.number}: de Follow-up-agent zoekt onbeantwoorde e-mails.", "success")
    return redirect(f"/app/ops/tasks/{task.id}")


# ---------------------------------------------------------------- inbox


@router.get("/app/inbox")
def inbox(request: Request, category: str = "", show: str = "open", user: User = Depends(require_staff),
          db: Session = Depends(get_db)):
    stmt = select(InboundMessage).options(selectinload(InboundMessage.prospect))
    if category in ReplyCategory.__members__:
        stmt = stmt.where(InboundMessage.category == ReplyCategory(category))
    if show == "open":
        stmt = stmt.where(InboundMessage.handled.is_(False))
    rows = db.scalars(stmt.order_by(InboundMessage.received_at.desc()).limit(300)).all()
    counts = dict(db.execute(select(InboundMessage.category, func.count()).group_by(InboundMessage.category)).all())
    drafts = {m.in_reply_to_id: m for m in db.scalars(select(OutreachMessage).where(
        OutreachMessage.in_reply_to_id.in_([r.id for r in rows]))).all()} if rows else {}
    return render(request, "crm/inbox.html", user=user, items=rows, counts=counts, category=category, show=show,
                  drafts=drafts, mail=provider_status(), categories=list(ReplyCategory),
                  total=db.scalar(select(func.count()).select_from(InboundMessage)) or 0)


@router.post("/app/inbox/register", dependencies=[Depends(verify_csrf)])
def inbox_register(request: Request, background: BackgroundTasks, from_email: str = Form(...),
                   from_name: str = Form(""), subject: str = Form(""), body: str = Form(...),
                   user: User = Depends(require_staff), db: Session = Depends(get_db)):
    from_email = from_email.strip().lower()
    if "@" not in from_email or not body.strip():
        flash(request, "Vul het afzenderadres en de tekst van het bericht in.", "error")
        return redirect("/app/inbox")
    msg = crm.register_inbound(db, from_email=from_email, from_name=from_name.strip() or None,
                               subject=subject.strip() or "(geen onderwerp)", body=body.strip()[:20000],
                               source="manual")
    task = enqueue(db, "email", "classify_reply", {"inbound_id": msg.id}, title=f"Classificeer reactie van "
                   f"{from_email}", created_by_user_id=user.id, priority=7)
    audit(db, "inbound.registered", user=user, object_type="inbound_message", object_id=msg.id)
    db.commit()
    _schedule(background)
    flash(request, "Reactie vastgelegd; de Email-agent classificeert hem." + (
        "" if msg.prospect_id else " Let op: het adres hoort (nog) niet bij een bekende lead."), "success")
    return redirect(f"/app/inbox/{msg.id}?task={task.id}")


@router.post("/app/inbox/simulate", dependencies=[Depends(verify_csrf)])
def inbox_simulate(request: Request, background: BackgroundTasks, message_id: str = Form(...), body: str = Form(...),
                   user: User = Depends(require_staff), db: Session = Depends(get_db)):
    """MOCK provider only: drop a reply to a sent message in the mock inbox, then let the Email agent read it."""
    provider = get_email_provider()
    if not isinstance(provider, MockEmailProvider):
        raise HTTPException(status_code=404)
    msg = _message(db, message_id)
    if msg.status != OutreachStatus.SENT or not body.strip():
        flash(request, "Alleen op een verstuurd bericht kan een antwoord worden gesimuleerd.", "error")
        return redirect(f"/app/outreach/{msg.id}")
    provider.simulate_incoming(from_email=msg.to_email, from_name=msg.to_name, subject=f"Re: {msg.subject}",
                               body=body.strip()[:5000], in_reply_to=msg.delivery_ref)
    task = enqueue(db, "email", "fetch_inbox", {}, title="Mailbox lezen (MOCK)", created_by_user_id=user.id,
                   priority=7)
    db.commit()
    _schedule(background)
    flash(request, "MOCK-antwoord in de mailbox gezet; de Email-agent leest en classificeert het.", "success")
    return redirect(f"/app/ops/tasks/{task.id}")


@router.post("/app/inbox/fetch", dependencies=[Depends(verify_csrf)])
def inbox_fetch(request: Request, background: BackgroundTasks, user: User = Depends(require_staff),
                db: Session = Depends(get_db)):
    task = enqueue(db, "email", "fetch_inbox", {}, title="Mailbox lezen", created_by_user_id=user.id, priority=6)
    db.commit()
    _schedule(background)
    return redirect(f"/app/ops/tasks/{task.id}")


@router.get("/app/inbox/{message_id}")
def inbox_message(message_id: str, request: Request, user: User = Depends(require_staff),
                  db: Session = Depends(get_db)):
    m = db.get(InboundMessage, message_id)
    if m is None:
        raise HTTPException(status_code=404)
    draft = db.scalar(select(OutreachMessage).where(OutreachMessage.in_reply_to_id == m.id))
    original = db.get(OutreachMessage, m.outreach_id) if m.outreach_id else None
    return render(request, "crm/inbound.html", user=user, m=m, p=m.prospect, draft=draft, original=original,
                  categories=list(ReplyCategory), pending=m.category is None,
                  pulse=pulse(db) if m.category is None else None)


@router.post("/app/inbox/{message_id}/update", dependencies=[Depends(verify_csrf)])
def inbox_update(message_id: str, request: Request, background: BackgroundTasks, category: str = Form(""),
                 handled: str = Form(""), user: User = Depends(require_staff), db: Session = Depends(get_db)):
    m = db.get(InboundMessage, message_id)
    if m is None:
        raise HTTPException(status_code=404)
    if category in ReplyCategory.__members__ and (m.category is None or category != m.category.value):
        from app.domain.replies import ReplyClassification

        crm.apply_classification(db, m, ReplyClassification(ReplyCategory(category), Confidence.HIGH,
                                                            [f"handmatig gecorrigeerd door {user.email}"]),
                                 agent_id="human", task_id=None)
        m.classified_by_agent_id = None
        flash(request, "Classificatie aangepast; de lead is bijgewerkt.", "success")
    if handled in ("1", "0"):
        m.handled = handled == "1"
    db.commit()
    return redirect("/app/inbox" if handled == "1" else f"/app/inbox/{m.id}")


# ---------------------------------------------------------------- settings


@router.get("/app/settings")
def settings_page(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    from app.models import Suppression

    s = get_settings()
    research = get_research_provider()
    mail = provider_status()
    env = [
        ("ER_RESEARCH_PROVIDER", s.research_provider,
         "openstreetmap = echte bedrijven (internet nodig); mock = testdata"),
        ("ER_EMAIL_PROVIDER", s.email_provider, "smtp = echt versturen en lezen; mock = niets verlaat deze computer"),
        ("ER_SMTP_HOST / ER_SMTP_PORT", f"{s.smtp_host or '—'} / {s.smtp_port}", "mailserver voor verzenden"),
        ("ER_SMTP_USER / ER_SMTP_PASSWORD", (s.smtp_user or "—") + (" / ••••" if s.smtp_password else " / —"),
         "inloggegevens mailserver"),
        ("ER_IMAP_HOST / ER_IMAP_USER / ER_IMAP_PASSWORD",
         f"{s.imap_host or '—'} / {s.imap_user or '—'}" + (" / ••••" if s.imap_password else " / —"),
         "mailbox waar antwoorden binnenkomen (alleen lezen)"),
        ("ER_OUTREACH_FROM_EMAIL / ER_OUTREACH_FROM_NAME", f"{mail['from']} / {s.outreach_from_name}",
         "afzender van uitgaande e-mail"),
        ("ER_RESEARCH_APPROVAL_THRESHOLD", str(s.research_approval_threshold),
         "grotere onderzoeken vragen eerst goedkeuring"),
        ("ER_LEAD_QUALIFY_THRESHOLD", str(s.lead_qualify_threshold), "fitscore vanaf waar een lead doorgaat"),
        ("ER_FOLLOW_UP_DAYS", str(s.follow_up_days), "dagen zonder reactie voor een opvolgconcept"),
        ("ER_AGENTS_AUTORUN", str(s.agents_autorun).lower(), "taken direct na aanmaken uitvoeren"),
    ]
    suppressions = db.scalars(select(Suppression).order_by(Suppression.created_at.desc()).limit(200)).all()
    return render(request, "crm/settings.html", user=user, research=research, mail=mail, env=env,
                  suppressions=suppressions)


@router.post("/app/settings/suppressions", dependencies=[Depends(verify_csrf)])
def settings_suppress(request: Request, email: str = Form(...), reason: str = Form(""),
                      user: User = Depends(require_staff), db: Session = Depends(get_db)):
    email = email.strip().lower()
    if "@" not in email:
        flash(request, "Vul een geldig e-mailadres in.", "error")
        return redirect("/app/settings#suppressie")
    crm.suppress_address(db, email, reason.strip() or "handmatig toegevoegd", source="manual", user=user)
    audit(db, "suppression.added", user=user, object_type="suppression", object_id=None)
    db.commit()
    flash(request, f"{email} staat op de niet-benaderen-lijst.", "success")
    return redirect("/app/settings#suppressie")
