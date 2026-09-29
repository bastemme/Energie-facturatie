"""Human review of findings. Every decision is recorded with reviewer, time and note."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.enums import ReviewStatus
from app.models import Anomaly, User
from app.models.base import utcnow
from app.services.audit import audit

REVIEW_ACTIONS = {
    "confirm": ReviewStatus.CONFIRMED,
    "reject": ReviewStatus.REJECTED,
    "investigate": ReviewStatus.INVESTIGATING,
    "request_info": ReviewStatus.INFO_REQUESTED,
    "duplicate": ReviewStatus.DUPLICATE,
    "resolve": ReviewStatus.RESOLVED,
    "reopen": ReviewStatus.OPEN,
}
NOTE_REQUIRED = {ReviewStatus.REJECTED, ReviewStatus.DUPLICATE, ReviewStatus.INFO_REQUESTED}

REVIEW_LABELS_NL = {
    ReviewStatus.OPEN: "Open", ReviewStatus.INVESTIGATING: "In onderzoek",
    ReviewStatus.INFO_REQUESTED: "Informatie opgevraagd", ReviewStatus.CONFIRMED: "Bevestigd",
    ReviewStatus.REJECTED: "Afgewezen", ReviewStatus.DUPLICATE: "Dubbel", ReviewStatus.RESOLVED: "Opgelost",
}


class ReviewError(Exception):
    pass


def review(db: Session, anomaly: Anomaly, action: str, user: User, note: str | None = None,
           duplicate_of: Anomaly | None = None, ip: str | None = None) -> None:
    status = REVIEW_ACTIONS.get(action)
    if status is None:
        raise ReviewError("Onbekende actie.")
    note = (note or "").strip() or None
    if status in NOTE_REQUIRED and not note:
        raise ReviewError("Een toelichting is verplicht bij deze beslissing.")
    if anomaly.case_id and status != ReviewStatus.CONFIRMED:
        raise ReviewError("Deze bevinding zit in een dossier; wijzig eerst het dossier.")
    if status == ReviewStatus.DUPLICATE:
        if duplicate_of is None or duplicate_of.client_id != anomaly.client_id or duplicate_of.id == anomaly.id:
            raise ReviewError("Geef aan van welke bevinding dit een duplicaat is.")
        anomaly.duplicate_of_id = duplicate_of.id
    anomaly.review_status = status
    anomaly.reviewed_by_id = user.id
    anomaly.reviewed_at = utcnow()
    if note:
        stamp = f"[{utcnow():%d-%m-%Y %H:%M} {user.email}] {REVIEW_LABELS_NL[status]}: {note}"
        anomaly.review_notes = f"{anomaly.review_notes}\n{stamp}" if anomaly.review_notes else stamp
    audit(db, "anomaly.review", user=user, client_id=anomaly.client_id, object_type="anomaly",
          object_id=anomaly.id, ip=ip, details={"status": status.value})
    db.flush()
