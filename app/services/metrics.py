"""Dashboard figures. Potential, confirmed and recovered money are always separate numbers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import CaseStatus, DocumentStatus, DocumentType, InvoiceType, ReviewStatus
from app.domain.money import ZERO, round_cents
from app.models import Anomaly, Document, Invoice, ProcessingEvent, RecoveryCase
from app.services.recovery import conservative_total

POTENTIAL_STATUSES = {ReviewStatus.OPEN, ReviewStatus.INVESTIGATING, ReviewStatus.INFO_REQUESTED,
                      ReviewStatus.CONFIRMED}
INVESTIGATING_CASES = {CaseStatus.DETECTED, CaseStatus.REVIEW, CaseStatus.VERIFIED, CaseStatus.CLIENT_APPROVAL}
SUBMITTED_CASES = {CaseStatus.SUBMITTED, CaseStatus.SUPPLIER_REVIEW, CaseStatus.NEGOTIATION, CaseStatus.APPROVED,
                   CaseStatus.DISPUTED}


@dataclass
class Summary:
    invoices_analyzed: int = 0
    total_invoice_value: Decimal = ZERO
    potential_discrepancies: Decimal = ZERO
    confirmed_discrepancies: Decimal = ZERO
    claimed_amount: Decimal = ZERO
    supplier_approved: Decimal = ZERO
    recovered: Decimal = ZERO
    success_fee: Decimal = ZERO
    open_findings: int = 0
    confirmed_findings: int = 0
    cases_investigating: int = 0
    cases_submitted: int = 0
    cases_recovered: int = 0
    findings_by_category: dict[str, int] = field(default_factory=dict)


def _scoped(stmt, model, client_id: str | None):
    return stmt.where(model.client_id == client_id) if client_id else stmt


def summary(db: Session, client_id: str | None = None) -> Summary:
    s = Summary()
    invoices = db.scalars(_scoped(select(Invoice), Invoice, client_id)).all()
    seen_numbers: set[tuple] = set()
    for inv in invoices:
        key = ((inv.supplier or "").lower(), inv.invoice_number, inv.total_incl_vat)
        if inv.invoice_number and key in seen_numbers:
            continue  # same invoice delivered twice
        seen_numbers.add(key)
        s.invoices_analyzed += 1
        if inv.invoice_type == InvoiceType.INVOICE and inv.total_incl_vat is not None:
            s.total_invoice_value += inv.total_incl_vat
    anomalies = db.scalars(_scoped(select(Anomaly).where(Anomaly.is_stale.is_(False)), Anomaly, client_id)).all()
    potential = [a for a in anomalies if a.review_status in POTENTIAL_STATUSES]
    confirmed = [a for a in anomalies if a.review_status == ReviewStatus.CONFIRMED]
    s.potential_discrepancies = conservative_total(potential)
    s.confirmed_discrepancies = conservative_total(confirmed)
    s.open_findings = sum(1 for a in anomalies if a.review_status in
                          (ReviewStatus.OPEN, ReviewStatus.INVESTIGATING, ReviewStatus.INFO_REQUESTED))
    s.confirmed_findings = len(confirmed)
    s.findings_by_category = dict(Counter(a.category for a in potential))
    cases = db.scalars(_scoped(select(RecoveryCase), RecoveryCase, client_id)).all()
    for c in cases:
        if c.submitted_at is not None:
            s.claimed_amount += c.disputed_amount or ZERO
        s.supplier_approved += c.confirmed_amount or ZERO
        s.recovered += c.recovered_amount or ZERO
        s.success_fee += c.success_fee or ZERO
        if c.status in INVESTIGATING_CASES:
            s.cases_investigating += 1
        elif c.status in SUBMITTED_CASES:
            s.cases_submitted += 1
        elif c.recovered_amount:
            s.cases_recovered += 1
    s.total_invoice_value = round_cents(s.total_invoice_value)
    return s


@dataclass
class OperatorMetrics:
    documents_total: int
    documents_by_status: dict[str, int]
    invoice_documents: int
    extraction_success_rate: float | None
    anomalies_detected: int
    anomalies_confirmed: int
    anomalies_rejected: int
    false_positive_rate: float | None
    review_queue: int
    avg_potential_per_invoice: Decimal
    recovered_per_1000_invoices: Decimal
    processing_cost_eur: float
    revenue: Decimal
    low_confidence_documents: int
    rule_stats: list[dict]


def operator_metrics(db: Session) -> OperatorMetrics:
    s = summary(db)
    status_counts = dict(db.execute(select(Document.status, func.count()).group_by(Document.status)).all())
    by_status = {k.value if hasattr(k, "value") else str(k): v for k, v in status_counts.items()}
    invoice_docs = db.scalar(select(func.count()).select_from(Document).where(
        Document.doc_type.in_([DocumentType.INVOICE, DocumentType.CREDIT_NOTE, DocumentType.INVOICE_TABLE]))) or 0
    extracted_ok = db.scalar(select(func.count()).select_from(Document).where(
        Document.doc_type.in_([DocumentType.INVOICE, DocumentType.CREDIT_NOTE, DocumentType.INVOICE_TABLE]),
        Document.status == DocumentStatus.PROCESSED)) or 0
    attempted = invoice_docs + (by_status.get("NEEDS_OCR", 0)) + (by_status.get("FAILED", 0))
    anomalies = db.scalars(select(Anomaly).where(Anomaly.is_stale.is_(False))).all()
    confirmed = sum(1 for a in anomalies if a.review_status == ReviewStatus.CONFIRMED)
    rejected = sum(1 for a in anomalies if a.review_status == ReviewStatus.REJECTED)
    queue = sum(1 for a in anomalies if a.review_status in (ReviewStatus.OPEN, ReviewStatus.INVESTIGATING,
                                                            ReviewStatus.INFO_REQUESTED))
    rules: dict[str, dict] = {}
    for a in anomalies:
        r = rules.setdefault(a.rule_id, {"rule_id": a.rule_id, "total": 0, "confirmed": 0, "rejected": 0})
        r["total"] += 1
        r["confirmed"] += a.review_status == ReviewStatus.CONFIRMED
        r["rejected"] += a.review_status == ReviewStatus.REJECTED
    for r in rules.values():
        decided = r["confirmed"] + r["rejected"]
        r["precision"] = round(r["confirmed"] / decided, 2) if decided else None
    cost = db.scalar(select(func.coalesce(func.sum(ProcessingEvent.cost_eur), 0.0))) or 0.0
    low_conf = db.scalar(select(func.count()).select_from(Document).where(
        Document.status.in_([DocumentStatus.NEEDS_REVIEW, DocumentStatus.NEEDS_OCR, DocumentStatus.FAILED]))) or 0
    n = s.invoices_analyzed
    return OperatorMetrics(
        documents_total=sum(by_status.values()), documents_by_status=by_status, invoice_documents=invoice_docs,
        extraction_success_rate=round(extracted_ok / attempted, 3) if attempted else None,
        anomalies_detected=len(anomalies), anomalies_confirmed=confirmed, anomalies_rejected=rejected,
        false_positive_rate=round(rejected / (confirmed + rejected), 3) if (confirmed + rejected) else None,
        review_queue=queue,
        avg_potential_per_invoice=round_cents(s.potential_discrepancies / n) if n else ZERO,
        recovered_per_1000_invoices=round_cents(s.recovered * 1000 / n) if n else ZERO,
        processing_cost_eur=float(cost), revenue=s.success_fee, low_confidence_documents=low_conf,
        rule_stats=sorted(rules.values(), key=lambda r: -r["total"]),
    )
