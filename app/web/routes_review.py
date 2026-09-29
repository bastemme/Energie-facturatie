"""Review queue and finding detail with evidence."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.domain.enums import Classification, Confidence, ReviewStatus
from app.models import Anomaly, Client, User
from app.services.recovery import conservative_total
from app.services.review import ReviewError, review
from app.web.deps import client_ip, flash, redirect, render
from app.web.security import get_owned, require_staff, require_user, verify_csrf

router = APIRouter()
QUEUE_DEFAULT = [ReviewStatus.OPEN, ReviewStatus.INVESTIGATING, ReviewStatus.INFO_REQUESTED]


@router.get("/app/review")
def review_queue(request: Request, client_id: str = "", status: str = "", confidence: str = "", rule: str = "",
                 classification: str = "", user: User = Depends(require_staff), db: Session = Depends(get_db)):
    q = select(Anomaly).where(Anomaly.is_stale.is_(False)).options(selectinload(Anomaly.invoice))
    if client_id:
        q = q.where(Anomaly.client_id == client_id)
    statuses = [ReviewStatus(status)] if status in ReviewStatus.__members__ else QUEUE_DEFAULT
    q = q.where(Anomaly.review_status.in_(statuses))
    if confidence in Confidence.__members__:
        q = q.where(Anomaly.confidence == Confidence(confidence))
    if classification in Classification.__members__:
        q = q.where(Anomaly.classification == Classification(classification))
    if rule:
        q = q.where(Anomaly.rule_id == rule)
    items = db.scalars(q.limit(1000)).all()
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    items = sorted(items, key=lambda a: (-a.potential_recovery, rank[a.confidence.value]))
    clients = {c.id: c for c in db.scalars(select(Client)).all()}
    rules = sorted({r for (r,) in db.execute(select(Anomaly.rule_id).distinct()).all()})
    return render(request, "review/queue.html", user=user, items=items, clients=clients, rules=rules,
                  total=conservative_total(items),
                  filters={"client_id": client_id, "status": status, "confidence": confidence, "rule": rule,
                           "classification": classification})


def evidence_links(anomaly: Anomaly) -> list[dict]:
    """Attach a 'Toon factuurpagina' link to every evidence item that points at a PDF page."""
    out = []
    for e in anomaly.evidence or []:
        item = dict(e)
        if e.get("document_id") and e.get("page"):
            hl = ",".join(str(v) for v in e["bbox"]) if e.get("bbox") else ""
            item["page_url"] = (f"/app/documents/{e['document_id']}/view/{e['page']}?hl={hl}"
                                f"&label={quote(str(e.get('label', ''))[:200])}")
        elif e.get("document_id"):
            item["doc_url"] = f"/app/documents/{e['document_id']}"
        out.append(item)
    return out


@router.get("/app/anomalies/{anomaly_id}")
def anomaly_detail(anomaly_id: str, request: Request, user: User = Depends(require_user),
                   db: Session = Depends(get_db)):
    a = get_owned(db, Anomaly, anomaly_id, user)
    if not user.is_staff and a.review_status != ReviewStatus.CONFIRMED:
        return render(request, "error.html", status_code=404, title="Niet gevonden")
    siblings = []
    if user.is_staff and a.invoice_id:
        siblings = db.scalars(select(Anomaly).where(Anomaly.invoice_id == a.invoice_id, Anomaly.id != a.id,
                                                    Anomaly.is_stale.is_(False))).all()
    return render(request, "review/detail.html", user=user, a=a, client=db.get(Client, a.client_id),
                  evidence=evidence_links(a), siblings=siblings)


@router.post("/app/anomalies/{anomaly_id}/review", dependencies=[Depends(verify_csrf)])
def anomaly_review(anomaly_id: str, request: Request, action: str = Form(...), note: str = Form(""),
                   duplicate_of: str = Form(""), next_url: str = Form(""), user: User = Depends(require_staff),
                   db: Session = Depends(get_db)):
    a = get_owned(db, Anomaly, anomaly_id, user)
    dup = get_owned(db, Anomaly, duplicate_of, user) if duplicate_of else None
    try:
        review(db, a, action, user, note, duplicate_of=dup, ip=client_ip(request))
        db.commit()
        flash(request, "Beoordeling vastgelegd.", "success")
    except ReviewError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return redirect(f"/app/anomalies/{a.id}")
    return redirect(next_url if next_url.startswith("/app/") else f"/app/anomalies/{a.id}")
