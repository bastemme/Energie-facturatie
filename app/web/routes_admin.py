"""Internal admin: operator dashboard, reference rates, leads, staff users, audit log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.domain.enums import DocumentStatus, LeadStatus, RateKind, ReviewStatus, Role
from app.models import AnalysisRun, Anomaly, AuditLog, Client, Document, Lead, ReferenceRate, User
from app.models.base import utcnow
from app.services.audit import audit
from app.services.metrics import operator_metrics, summary
from app.web.deps import flash, form_date, form_decimal, redirect, render
from app.web.security import hash_password, require_admin, require_staff, verify_csrf
from app.web.view import bar_chart, category_distribution, monthly_series, pipeline_stages

router = APIRouter()


@router.get("/app/admin")
def admin_dashboard(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    problem_docs = db.scalars(select(Document).where(Document.status.in_(
        [DocumentStatus.NEEDS_REVIEW, DocumentStatus.NEEDS_OCR, DocumentStatus.FAILED]))
        .order_by(Document.created_at.desc()).limit(8)).all()
    failed_runs = [r for r in db.scalars(select(AnalysisRun).where(AnalysisRun.errors.is_not(None))
                                         .order_by(AnalysisRun.created_at.desc()).limit(10)).all() if r.errors]
    clients = db.scalars(select(Client).order_by(Client.company_name)).all()
    anomalies = db.scalars(select(Anomaly).where(Anomaly.is_stale.is_(False))
                           .options(selectinload(Anomaly.invoice))).all()
    queue = sorted([a for a in anomalies if a.review_status in (ReviewStatus.OPEN, ReviewStatus.INVESTIGATING,
                                                                ReviewStatus.INFO_REQUESTED)],
                   key=lambda a: -a.potential_recovery)
    s = summary(db)
    series = monthly_series(db, None)
    return render(request, "admin/dashboard.html", user=user, s=s, m=operator_metrics(db), problem_docs=problem_docs,
                  failed_runs=failed_runs, clients={c.id: c for c in clients},
                  client_rows=[(c, summary(db, c.id)) for c in clients], queue=queue[:4], queue_total=len(queue),
                  stages=pipeline_stages(s), distribution=category_distribution(anomalies),
                  bar=bar_chart(series) if series else None,
                  new_leads=db.scalars(select(Lead).where(Lead.status == LeadStatus.NEW)
                                       .order_by(Lead.created_at.desc())).all())


@router.get("/app/designsysteem")
def design_system(request: Request, user: User = Depends(require_staff)):
    return render(request, "admin/design.html", user=user)


@router.get("/app/reference-rates")
def rates_list(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    rates = db.scalars(select(ReferenceRate).order_by(ReferenceRate.kind, ReferenceRate.valid_from.desc(),
                                                      ReferenceRate.bracket_from)).all()
    return render(request, "admin/rates.html", user=user, rates=rates, kinds=list(RateKind))


@router.post("/app/reference-rates", dependencies=[Depends(verify_csrf)])
def rates_add(request: Request, kind: str = Form(...), rate: str = Form(...), unit: str = Form(...),
              valid_from: str = Form(...), valid_to: str = Form(""), bracket_from: str = Form(""),
              bracket_to: str = Form(""), source_reference: str = Form(""), notes: str = Form(""),
              user: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        if kind not in RateKind.__members__:
            raise ValueError("Onbekend soort tarief.")
        if not source_reference.strip():
            raise ValueError("Een bronvermelding (URL of wetsartikel) is verplicht.")
        r = ReferenceRate(kind=RateKind(kind), rate=form_decimal(rate), unit=unit.strip()[:20],
                          valid_from=form_date(valid_from), valid_to=form_date(valid_to),
                          bracket_from=form_decimal(bracket_from), bracket_to=form_decimal(bracket_to),
                          source_reference=source_reference.strip()[:1000], notes=notes.strip() or None)
        if r.rate is None or r.valid_from is None:
            raise ValueError("Tarief en ingangsdatum zijn verplicht.")
    except ValueError as exc:
        flash(request, str(exc), "error")
        return redirect("/app/reference-rates")
    db.add(r)
    db.flush()
    audit(db, "reference_rate.created", user=user, object_type="reference_rate", object_id=r.id)
    db.commit()
    flash(request, "Tarief toegevoegd. Het wordt pas gebruikt nadat het expliciet is geverifieerd.", "success")
    return redirect("/app/reference-rates")


@router.post("/app/reference-rates/{rate_id}/verify", dependencies=[Depends(verify_csrf)])
def rates_verify(rate_id: str, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    r = db.get(ReferenceRate, rate_id)
    if r is None:
        return redirect("/app/reference-rates")
    r.verified_at = utcnow()
    r.verified_by_id = user.id
    audit(db, "reference_rate.verified", user=user, object_type="reference_rate", object_id=r.id)
    db.commit()
    flash(request, "Tarief geverifieerd; het wordt gebruikt bij de volgende analyse.", "success")
    return redirect("/app/reference-rates")


@router.get("/app/leads")
def leads_list(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    leads = db.scalars(select(Lead).order_by(Lead.created_at.desc()).limit(500)).all()
    return render(request, "admin/leads.html", user=user, leads=leads, statuses=list(LeadStatus))


@router.post("/app/leads/{lead_id}/status", dependencies=[Depends(verify_csrf)])
def lead_status(lead_id: str, request: Request, status: str = Form(...), user: User = Depends(require_staff),
                db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if lead and status in LeadStatus.__members__:
        lead.status = LeadStatus(status)
        audit(db, "lead.status", user=user, object_type="lead", object_id=lead.id, details={"status": status})
        db.commit()
    return redirect("/app/leads")


@router.get("/app/users")
def users_list(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.role, User.email)).all()
    return render(request, "admin/users.html", user=user, users=users,
                  clients={c.id: c for c in db.scalars(select(Client)).all()})


@router.post("/app/users", dependencies=[Depends(verify_csrf)])
def users_add(request: Request, email: str = Form(...), full_name: str = Form(""), role: str = Form(...),
              password: str = Form(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    email = email.strip().lower()
    if role not in (Role.ADMIN.value, Role.REVIEWER.value):
        flash(request, "Klantgebruikers maakt u aan vanuit de klantpagina.", "error")
        return redirect("/app/users")
    if db.scalar(select(User).where(User.email == email)):
        flash(request, "Dit e-mailadres is al in gebruik.", "error")
        return redirect("/app/users")
    try:
        new = User(email=email, full_name=full_name or None, password_hash=hash_password(password), role=Role(role))
    except ValueError as exc:
        flash(request, str(exc), "error")
        return redirect("/app/users")
    db.add(new)
    db.flush()
    audit(db, "user.created", user=user, object_type="user", object_id=new.id, details={"role": role})
    db.commit()
    return redirect("/app/users")


@router.post("/app/users/{user_id}/deactivate", dependencies=[Depends(verify_csrf)])
def users_deactivate(user_id: str, request: Request, user: User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target and target.id != user.id:
        target.is_active = False
        audit(db, "user.deactivated", user=user, object_type="user", object_id=target.id)
        db.commit()
    return redirect("/app/users")


@router.get("/app/audit")
def audit_log(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(500)).all()
    users = {u.id: u for u in db.scalars(select(User)).all()}
    return render(request, "admin/audit.html", user=user, rows=rows, users=users)
