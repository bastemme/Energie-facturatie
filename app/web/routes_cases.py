"""Recovery cases: creation, lifecycle, amounts, correspondence and claim package."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.domain.enums import CaseStatus, ReviewStatus
from app.models import Anomaly, Client, Document, Invoice, RecoveryCase, User
from app.services.audit import audit
from app.services.cases import (
    CaseError,
    add_note,
    allowed_transitions,
    create_case,
    recompute_disputed,
    set_confirmed_amount,
    set_recovered_amount,
    transition,
)
from app.services.correspondence import build_items, generate_letter
from app.services.reports import claim_package_zip, invoices_by_id
from app.web.deps import attachment, client_ip, flash, form_decimal, redirect, render
from app.web.security import get_client_for, get_owned, require_staff, require_user, verify_csrf

router = APIRouter()


def claim_items_for(db: Session, case: RecoveryCase):
    ids = [a.invoice_id for a in case.anomalies if a.invoice_id]
    invoices = db.scalars(select(Invoice).where(Invoice.id.in_(ids))).all() if ids else []
    return build_items(case, invoices_by_id(list(invoices)))


@router.get("/app/cases")
def cases_list(request: Request, status: str = "", user: User = Depends(require_staff), db: Session = Depends(get_db)):
    q = select(RecoveryCase).order_by(RecoveryCase.created_at.desc())
    if status in CaseStatus.__members__:
        q = q.where(RecoveryCase.status == CaseStatus(status))
    cases = db.scalars(q).all()
    clients = {c.id: c for c in db.scalars(select(Client)).all()}
    return render(request, "cases/list.html", user=user, cases=cases, clients=clients, status=status)


@router.get("/app/clients/{client_id}/cases/new")
def case_new(client_id: str, request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    client = get_client_for(db, user, client_id)
    confirmed = db.scalars(select(Anomaly).where(Anomaly.client_id == client.id,
                                                 Anomaly.review_status == ReviewStatus.CONFIRMED,
                                                 Anomaly.case_id.is_(None), Anomaly.is_stale.is_(False))).all()
    return render(request, "cases/new.html", user=user, client=client, anomalies=confirmed)


@router.post("/app/clients/{client_id}/cases", dependencies=[Depends(verify_csrf)])
async def case_create(client_id: str, request: Request, user: User = Depends(require_staff),
                      db: Session = Depends(get_db)):
    client = get_client_for(db, user, client_id)
    form = await request.form()
    ids = [str(v) for v in form.getlist("anomaly_ids")]
    anomalies = [get_owned(db, Anomaly, i, user) for i in ids]
    try:
        case = create_case(db, client, anomalies, user, note=str(form.get("note", "")).strip() or None)
        audit(db, "case.created", user=user, client_id=client.id, object_type="case", object_id=case.id)
        db.commit()
    except CaseError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return redirect(f"/app/clients/{client.id}/cases/new")
    return redirect(f"/app/cases/{case.id}")


@router.get("/app/cases/{case_id}")
def case_detail(case_id: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    case = get_owned(db, RecoveryCase, case_id, user)
    client = db.get(Client, case.client_id)
    items = claim_items_for(db, case)
    letter = generate_letter(case, client, items, get_settings().operator_name) if user.is_staff else None
    return render(request, "cases/detail.html", user=user, case=case, client=client, items=items, letter=letter,
                  transitions=allowed_transitions(case))


@router.post("/app/cases/{case_id}/transition", dependencies=[Depends(verify_csrf)])
def case_transition(case_id: str, request: Request, to_status: str = Form(...), note: str = Form(""),
                    user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = get_owned(db, RecoveryCase, case_id, user)
    try:
        if to_status not in CaseStatus.__members__:
            raise CaseError("Onbekende status.")
        transition(db, case, CaseStatus(to_status), user, note.strip() or None)
        audit(db, "case.transition", user=user, client_id=case.client_id, object_type="case", object_id=case.id,
              details={"to": to_status})
        db.commit()
    except CaseError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return redirect(f"/app/cases/{case.id}")


@router.post("/app/cases/{case_id}/amounts", dependencies=[Depends(verify_csrf)])
def case_amounts(case_id: str, request: Request, confirmed_amount: str = Form(""), recovered_amount: str = Form(""),
                 recovered_reference: str = Form(""), note: str = Form(""), user: User = Depends(require_staff),
                 db: Session = Depends(get_db)):
    case = get_owned(db, RecoveryCase, case_id, user)
    try:
        confirmed = form_decimal(confirmed_amount)
        recovered = form_decimal(recovered_amount)
        if confirmed is not None and confirmed != case.confirmed_amount:
            set_confirmed_amount(db, case, confirmed, user, note or None)
        if recovered is not None and recovered != case.recovered_amount:
            set_recovered_amount(db, case, recovered, recovered_reference.strip(), user, note or None)
        audit(db, "case.amounts_updated", user=user, client_id=case.client_id, object_type="case", object_id=case.id)
        db.commit()
        flash(request, "Bedragen bijgewerkt.", "success")
    except (CaseError, ValueError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return redirect(f"/app/cases/{case.id}")


@router.post("/app/cases/{case_id}/notes", dependencies=[Depends(verify_csrf)])
def case_note(case_id: str, request: Request, note: str = Form(...), user: User = Depends(require_staff),
              db: Session = Depends(get_db)):
    case = get_owned(db, RecoveryCase, case_id, user)
    if note.strip():
        add_note(db, case, note.strip()[:5000], user)
        db.commit()
    return redirect(f"/app/cases/{case.id}")


@router.post("/app/cases/{case_id}/remove/{anomaly_id}", dependencies=[Depends(verify_csrf)])
def case_remove_item(case_id: str, anomaly_id: str, request: Request, user: User = Depends(require_staff),
                     db: Session = Depends(get_db)):
    case = get_owned(db, RecoveryCase, case_id, user)
    a = get_owned(db, Anomaly, anomaly_id, user)
    if case.submitted_at is not None:
        flash(request, "Een ingediend dossier kan niet meer worden gewijzigd.", "error")
    elif a.case_id == case.id:
        a.case_id = None
        db.flush()
        db.refresh(case)
        recompute_disputed(case)
        add_note(db, case, f"Bevinding verwijderd uit dossier: {a.title}", user)
        db.commit()
    return redirect(f"/app/cases/{case.id}")


@router.get("/app/cases/{case_id}/package.zip")
def case_package(case_id: str, request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = get_owned(db, RecoveryCase, case_id, user)
    client = db.get(Client, case.client_id)
    items = claim_items_for(db, case)
    letter = generate_letter(case, client, items, get_settings().operator_name)
    doc_ids = {i.invoice.document_id for i in items if i.invoice and i.invoice.document_id}
    documents = db.scalars(select(Document).where(Document.id.in_(doc_ids))).all() if doc_ids else []
    data = claim_package_zip(case, client, letter, items, list(documents))
    audit(db, "case.package_exported", user=user, client_id=case.client_id, object_type="case", object_id=case.id,
          ip=client_ip(request))
    add_note(db, case, "Claimpakket gegenereerd.", user)
    db.commit()
    return Response(data, media_type="application/zip",
                    headers=attachment(f"{case.reference}.zip"))
