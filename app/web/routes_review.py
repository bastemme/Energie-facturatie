"""Review queue and finding detail with evidence."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.domain.enums import Classification, Confidence, ReviewStatus
from app.models import Anomaly, Client, Document, User
from app.services.audit import audit
from app.services.recovery import conservative_total
from app.services.review import ReviewError, review
from app.web.deps import client_ip, flash, redirect, render
from app.web.security import can_access_client, get_owned, require_staff, require_user, verify_csrf
from app.web.view import box_for, ensure_page_sizes, finding_view

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
    """Evidence items with stable ids, and a link to the source page where one exists."""
    out = []
    for n, e in enumerate(anomaly.evidence or []):
        item = dict(e)
        item["id"] = f"ev{n}"
        item["box_id"] = f"hl{n}" if e.get("bbox") and e.get("page") else None
        if e.get("document_id") and e.get("page"):
            hl = ",".join(str(v) for v in e["bbox"]) if e.get("bbox") else ""
            item["page_url"] = (f"/app/documents/{e['document_id']}/view/{e['page']}?hl={hl}"
                                f"&label={quote(str(e.get('label', ''))[:200])}")
        elif e.get("document_id"):
            item["doc_url"] = f"/app/documents/{e['document_id']}"
        out.append(item)
    return out


EV_ICON = {"invoice_line": "row", "invoice_field": "invoice", "contract_price": "contract", "meter_reading": "meter",
           "reference_rate": "rate"}


def source_view(db: Session, user: User, evidence: list[dict], doc_id: str | None = None,
                page: int | None = None) -> dict | None:
    """Pick the document page to show next to the analysis and the regions to highlight on it."""
    candidates = [e for e in evidence if e.get("document_id")]
    if not candidates:
        return None
    if doc_id is None:
        with_page = [e for e in candidates if e.get("page")]
        first = (with_page or candidates)[0]
        doc_id, page = first["document_id"], first.get("page")
    doc = db.get(Document, doc_id)
    if doc is None or not can_access_client(user, doc.client_id):
        return None
    sizes = ensure_page_sizes(db, doc)
    page = page or 1
    view = {"doc": doc, "page": page, "size": None, "boxes": [], "rows": []}
    if sizes and 1 <= page <= len(sizes):
        view["size"] = sizes[page - 1]
        for e in evidence:
            if e.get("document_id") == doc.id and e.get("page") == page and e.get("box_id"):
                box = box_for(e.get("bbox"), e["box_id"], label=e.get("label", ""))
                if box:
                    view["boxes"].append(box)
    else:
        view["rows"] = [e for e in evidence if e.get("document_id") == doc.id and e.get("source_text")]
    return view


@router.get("/app/anomalies/{anomaly_id}")
def anomaly_detail(anomaly_id: str, request: Request, doc: str | None = None, page: int | None = None,
                   user: User = Depends(require_user), db: Session = Depends(get_db)):
    a = get_owned(db, Anomaly, anomaly_id, user)
    if not user.is_staff and a.review_status != ReviewStatus.CONFIRMED:
        raise HTTPException(status_code=404)
    siblings = []
    if user.is_staff and a.invoice_id:
        siblings = db.scalars(select(Anomaly).where(Anomaly.invoice_id == a.invoice_id, Anomaly.id != a.id,
                                                    Anomaly.is_stale.is_(False))).all()
    evidence = evidence_links(a)
    audit(db, "anomaly.viewed", user=user, client_id=a.client_id, object_type="anomaly", object_id=a.id)
    db.commit()
    return render(request, "review/detail.html", user=user, a=a, client=db.get(Client, a.client_id),
                  evidence=evidence, source=source_view(db, user, evidence, doc, page), siblings=siblings,
                  ev_icon=EV_ICON, v=finding_view(a))


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
