"""Recovery-case lifecycle (explicit state machine) and amounts.

Amounts are strictly separated: disputed (what we claim), confirmed (what the supplier accepted),
recovered (money actually received — the only basis for the success fee).
"""

from __future__ import annotations

import secrets
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import CaseStatus as S
from app.domain.enums import ReviewStatus
from app.domain.fees import calculate_success_fee
from app.domain.money import ZERO, round_cents
from app.models import Anomaly, CaseEvent, Client, Invoice, RecoveryCase, User
from app.models.base import new_id, utcnow
from app.services.recovery import conservative_total


class CaseError(Exception):
    pass


TRANSITIONS: dict[S, set[S]] = {
    S.DETECTED: {S.REVIEW, S.REJECTED, S.INSUFFICIENT_EVIDENCE},
    S.REVIEW: {S.VERIFIED, S.REJECTED, S.INSUFFICIENT_EVIDENCE},
    S.VERIFIED: {S.CLIENT_APPROVAL, S.REVIEW, S.REJECTED},
    S.CLIENT_APPROVAL: {S.SUBMITTED, S.VERIFIED, S.REJECTED},
    S.SUBMITTED: {S.SUPPLIER_REVIEW, S.NEGOTIATION, S.APPROVED, S.DISPUTED, S.REJECTED},
    S.SUPPLIER_REVIEW: {S.NEGOTIATION, S.APPROVED, S.DISPUTED, S.REJECTED},
    S.NEGOTIATION: {S.APPROVED, S.DISPUTED, S.REJECTED},
    S.DISPUTED: {S.NEGOTIATION, S.APPROVED, S.REJECTED, S.CLOSED},
    S.APPROVED: {S.RECOVERED, S.DISPUTED},
    S.RECOVERED: {S.CLOSED},
    S.INSUFFICIENT_EVIDENCE: {S.REVIEW, S.CLOSED},
    S.REJECTED: {S.REVIEW, S.CLOSED},
    S.CLOSED: set(),
}

STATUS_LABELS_NL = {
    S.DETECTED: "Gedetecteerd", S.REVIEW: "In beoordeling", S.VERIFIED: "Geverifieerd",
    S.CLIENT_APPROVAL: "Wacht op akkoord klant", S.SUBMITTED: "Ingediend bij leverancier",
    S.SUPPLIER_REVIEW: "In behandeling bij leverancier", S.NEGOTIATION: "In onderhandeling",
    S.APPROVED: "Toegekend door leverancier", S.RECOVERED: "Teruggevorderd", S.CLOSED: "Gesloten",
    S.REJECTED: "Afgewezen", S.DISPUTED: "Betwist", S.INSUFFICIENT_EVIDENCE: "Onvoldoende bewijs",
}
OPEN_STATUSES = set(S) - {S.CLOSED, S.REJECTED, S.RECOVERED}


def allowed_transitions(case: RecoveryCase) -> list[S]:
    return sorted(TRANSITIONS[case.status], key=lambda s: list(S).index(s))


def _event(case: RecoveryCase, user: User | None, event_type: str, *, note: str | None = None,
           from_status: S | None = None, to_status: S | None = None, data: dict | None = None) -> None:
    case.events.append(CaseEvent(
        id=new_id(), user_id=user.id if user else None, event_type=event_type, note=note,
        from_status=from_status.value if from_status else None, to_status=to_status.value if to_status else None,
        data=data,
    ))


def _reference(db: Session) -> str:
    for _ in range(10):
        ref = f"ER-{utcnow():%Y}-{secrets.token_hex(3).upper()}"
        if not db.scalar(select(RecoveryCase.id).where(RecoveryCase.reference == ref)):
            return ref
    raise CaseError("could not allocate case reference")


def create_case(db: Session, client: Client, anomalies: list[Anomaly], user: User | None,
                note: str | None = None) -> RecoveryCase:
    if not anomalies:
        raise CaseError("Selecteer ten minste één bevinding.")
    if any(a.client_id != client.id for a in anomalies):
        raise CaseError("Bevindingen horen niet bij deze klant.")
    if any(a.review_status != ReviewStatus.CONFIRMED for a in anomalies):
        raise CaseError("Alleen bevestigde bevindingen kunnen in een dossier worden opgenomen.")
    if any(a.case_id for a in anomalies):
        raise CaseError("Eén of meer bevindingen zitten al in een dossier.")
    suppliers = {_supplier_of(db, a) for a in anomalies}
    if len(suppliers) != 1 or None in suppliers:
        raise CaseError("Een dossier bevat bevindingen van precies één leverancier.")
    case = RecoveryCase(
        client_id=client.id, reference=_reference(db), supplier=suppliers.pop(), status=S.REVIEW,
        disputed_amount=conservative_total(anomalies), success_fee_percentage=client.success_fee_percentage,
        created_by_id=user.id if user else None, notes=note,
    )
    db.add(case)
    db.flush()
    for a in anomalies:
        a.case_id = case.id
    _event(case, user, "CREATED", to_status=S.REVIEW, note=note,
           data={"anomaly_ids": [a.id for a in anomalies]})
    db.flush()
    return case


def _supplier_of(db: Session, anomaly: Anomaly) -> str | None:
    if anomaly.invoice_id is None:
        return None
    inv = db.get(Invoice, anomaly.invoice_id)
    return inv.supplier if inv else None


def recompute_disputed(case: RecoveryCase) -> None:
    case.disputed_amount = conservative_total(case.anomalies)


def transition(db: Session, case: RecoveryCase, to: S, user: User | None, note: str | None = None) -> None:
    if to not in TRANSITIONS[case.status]:
        raise CaseError(f"Overgang {case.status.value} → {to.value} is niet toegestaan.")
    if to == S.APPROVED and case.confirmed_amount is None:
        raise CaseError("Vul eerst het door de leverancier toegekende bedrag in.")
    if to == S.RECOVERED and not (case.recovered_amount and case.recovered_amount > ZERO):
        raise CaseError("Vul eerst het daadwerkelijk ontvangen bedrag in.")
    if to in (S.REJECTED, S.DISPUTED, S.INSUFFICIENT_EVIDENCE) and not note:
        raise CaseError("Geef een toelichting bij deze status.")
    previous = case.status
    case.status = to
    now = utcnow()
    if to == S.SUBMITTED and case.submitted_at is None:
        recompute_disputed(case)
        case.submitted_at = now
    if to == S.RECOVERED:
        case.recovered_at = now
    if to == S.CLOSED:
        case.closed_at = now
    _event(case, user, "STATUS_CHANGE", from_status=previous, to_status=to, note=note)
    db.flush()


def set_confirmed_amount(db: Session, case: RecoveryCase, amount: Decimal, user: User | None,
                         note: str | None = None) -> None:
    if amount < ZERO:
        raise CaseError("Bedrag kan niet negatief zijn.")
    case.confirmed_amount = round_cents(amount)
    _event(case, user, "AMOUNT_UPDATE", note=note, data={"field": "confirmed_amount"})
    db.flush()


def set_recovered_amount(db: Session, case: RecoveryCase, amount: Decimal, reference: str | None,
                         user: User | None, note: str | None = None) -> None:
    if amount < ZERO:
        raise CaseError("Bedrag kan niet negatief zijn.")
    if not reference:
        raise CaseError("Vermeld een referentie (creditnota- of betalingskenmerk) als bewijs van ontvangst.")
    fee = calculate_success_fee(amount, case.success_fee_percentage)
    case.recovered_amount = fee.recovered_amount
    case.recovered_reference = reference[:200]
    case.success_fee = fee.success_fee
    _event(case, user, "AMOUNT_UPDATE", note=note, data={"field": "recovered_amount"})
    db.flush()


def add_note(db: Session, case: RecoveryCase, note: str, user: User | None) -> None:
    _event(case, user, "NOTE", note=note)
    db.flush()
