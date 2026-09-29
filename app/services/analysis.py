"""Runs the detection engine for a client and persists findings idempotently."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.detection.base import AnalysisContext, DetectionSettings, Finding
from app.detection.registry import run_detectors
from app.domain.enums import ReviewStatus
from app.models import (
    AnalysisRun,
    Anomaly,
    Client,
    Contract,
    ExtractedValue,
    Invoice,
    MeterReading,
    ProcessingEvent,
    ReferenceRate,
    User,
)

# Review states in which a finding may still be refreshed by a new run
REFRESHABLE = {ReviewStatus.OPEN, ReviewStatus.INVESTIGATING, ReviewStatus.INFO_REQUESTED}


def detection_settings(client: Client) -> DetectionSettings:
    s = get_settings()
    return DetectionSettings(
        line_amount_tolerance=s.line_amount_tolerance,
        total_tolerance=s.total_tolerance,
        unit_price_tolerance=s.unit_price_tolerance,
        min_potential_recovery=s.min_potential_recovery,
        consumption_spike_ratio=s.consumption_spike_ratio,
        consumption_drop_ratio=s.consumption_drop_ratio,
        min_history_periods=s.min_history_periods,
        period_boundary_tolerance_days=s.period_boundary_tolerance_days,
        vat_deductible=client.vat_deductible,
    )


def build_context(db: Session, client: Client) -> AnalysisContext:
    invoices = list(db.scalars(
        select(Invoice).where(Invoice.client_id == client.id).options(selectinload(Invoice.lines))
    ).all())
    contracts = list(db.scalars(
        select(Contract).where(Contract.client_id == client.id).options(selectinload(Contract.prices))
    ).all())
    readings = list(db.scalars(select(MeterReading).where(MeterReading.client_id == client.id)).all())
    rates = list(db.scalars(select(ReferenceRate).where(ReferenceRate.verified_at.is_not(None))).all())
    inv_ids = [i.id for i in invoices]
    provenance = {}
    if inv_ids:
        for ev in db.scalars(select(ExtractedValue).where(ExtractedValue.invoice_id.in_(inv_ids))).all():
            provenance[(ev.invoice_id, ev.field_name)] = ev
    return AnalysisContext(
        client_id=client.id, invoices=invoices, contracts=contracts, meter_readings=readings,
        reference_rates=rates, provenance=provenance, settings=detection_settings(client),
    )


def run_analysis(db: Session, client: Client, user: User | None = None) -> AnalysisRun:
    ctx = build_context(db, client)
    result = run_detectors(ctx)
    run = AnalysisRun(
        client_id=client.id, started_by_id=user.id if user else None, rule_versions=result.rule_versions,
        invoices_analyzed=len(ctx.active_invoices), findings_total=len(result.findings),
        duration_ms=result.duration_ms, errors=result.errors or None,
    )
    db.add(run)
    db.flush()

    existing = {a.fingerprint: a for a in db.scalars(select(Anomaly).where(Anomaly.client_id == client.id)).all()}
    seen: set[str] = set()
    new = 0
    for f in result.findings:
        fp = f.fingerprint
        if fp in seen:
            continue
        seen.add(fp)
        anomaly = existing.get(fp)
        if anomaly is None:
            anomaly = Anomaly(client_id=client.id, fingerprint=fp, review_status=ReviewStatus.OPEN)
            db.add(anomaly)
            new += 1
            _apply(anomaly, f, run)
        elif anomaly.review_status in REFRESHABLE:
            _apply(anomaly, f, run)
        anomaly.is_stale = False
    for fp, anomaly in existing.items():
        if fp not in seen and anomaly.review_status in REFRESHABLE:
            anomaly.is_stale = True  # no longer reproduced (e.g. data corrected); kept for audit
    run.findings_new = new
    db.add(ProcessingEvent(client_id=client.id, step="analysis", duration_ms=result.duration_ms,
                           success=not result.errors, meta={"findings": len(result.findings), "new": new}))
    db.flush()
    return run


def _apply(a: Anomaly, f: Finding, run: AnalysisRun) -> None:
    a.invoice_id = f.invoice.id if f.invoice else None
    a.invoice_line_id = f.line.id if f.line else None
    a.analysis_run_id = run.id
    a.rule_id = f.rule_id
    a.rule_version = f.rule_version
    a.category = f.category
    a.classification = f.classification
    a.severity = f.severity
    a.confidence = f.confidence
    a.title = f.title[:300]
    a.description = f.description
    a.reason = f.reason
    a.unit = f.unit
    a.actual_value = f.actual
    a.expected_value = f.expected
    a.difference = f.difference
    a.potential_recovery = f.potential_recovery
    a.calculation = f.calculation
    a.evidence = f.evidence
    a.requires_verification = f.requires_verification
    a.extraction_confidence = f.invoice.extraction_confidence if f.invoice else None
