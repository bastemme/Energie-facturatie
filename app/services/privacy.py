"""GDPR support: data export (access/portability), erasure, and retention."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.storage import get_store
from app.models import (
    Anomaly,
    Client,
    Contract,
    Document,
    Invoice,
    MeterReading,
    RecoveryCase,
    User,
)
from app.models.base import utcnow
from app.services.audit import audit


def _row(obj, exclude: set[str] | None = None) -> dict:
    exclude = (exclude or set()) | {"password_hash", "storage_key"}
    out = {}
    for col in obj.__table__.columns:
        if col.name in exclude:
            continue
        v = getattr(obj, col.name)
        if isinstance(v, Decimal):
            v = str(v)
        elif hasattr(v, "isoformat"):
            v = v.isoformat()
        elif hasattr(v, "value"):
            v = v.value
        out[col.name] = v
    return out


def export_client_data(db: Session, client: Client) -> dict:
    cid = client.id

    def rows(model):
        return [_row(o) for o in db.scalars(select(model).where(model.client_id == cid)).all()]

    invoices = db.scalars(select(Invoice).where(Invoice.client_id == cid)).all()
    contracts = db.scalars(select(Contract).where(Contract.client_id == cid)).all()
    cases = db.scalars(select(RecoveryCase).where(RecoveryCase.client_id == cid)).all()
    return {
        "schema": "energy-recovery.client-export/v1",
        "exported_at": utcnow().isoformat(),
        "client": _row(client),
        "users": [_row(u) for u in db.scalars(select(User).where(User.client_id == cid)).all()],
        "documents": rows(Document),
        "invoices": [{**_row(i), "lines": [_row(li) for li in i.lines]} for i in invoices],
        "meter_readings": rows(MeterReading),
        "contracts": [{**_row(c), "prices": [_row(p) for p in c.prices]} for c in contracts],
        "anomalies": rows(Anomaly),
        "recovery_cases": [{**_row(c), "events": [_row(e) for e in c.events]} for c in cases],
    }


def erase_client(db: Session, client: Client, actor: User | None, reason: str) -> dict:
    """Hard-delete a client with all documents (incl. encrypted files) and derived data.

    The audit log keeps a record of the deletion (ids and counts only).
    """
    store = get_store()
    docs = db.scalars(select(Document).where(Document.client_id == client.id)).all()
    for d in docs:
        store.delete(d.storage_key)
    counts = {"documents": len(docs),
              "invoices": len(db.scalars(select(Invoice.id).where(Invoice.client_id == client.id)).all())}
    client_id = client.id
    # Delete dependants explicitly (in order) so this also works where FK cascades are not enforced.
    for model in (Anomaly, RecoveryCase, MeterReading, Contract, Invoice, Document, User):
        for obj in db.scalars(select(model).where(model.client_id == client_id)).all():
            db.delete(obj)
        db.flush()
    db.delete(client)
    audit(db, "client.erased", user=actor, client_id=client_id, object_type="client", object_id=client_id,
          details={**counts, "reason": reason[:100]})
    db.flush()
    return counts


def clients_due_for_retention(db: Session) -> list[Client]:
    now = utcnow()
    due = []
    for c in db.scalars(select(Client).where(Client.engagement_ended_at.is_not(None))).all():
        ended = c.engagement_ended_at if c.engagement_ended_at.tzinfo else c.engagement_ended_at.replace(
            tzinfo=now.tzinfo)
        if ended + timedelta(days=c.retention_days) <= now:
            due.append(c)
    return due


def run_retention(db: Session) -> list[str]:
    erased = []
    for client in clients_due_for_retention(db):
        erase_client(db, client, None, "retention period expired")
        erased.append(client.id)
    return erased
