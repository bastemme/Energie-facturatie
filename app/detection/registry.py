"""Registry and runner. Adding a rule = adding a module with a DETECTORS list and listing it here."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from app.detection import (
    arithmetic,
    consumption,
    contracts,
    credits,
    duplicates,
    energy_tax,
    fixed_charges,
    periods,
    vat,
)
from app.detection.base import AnalysisContext, Detector, Finding
from app.detection.duplicates import mark_data_duplicates

log = logging.getLogger(__name__)

ALL_DETECTORS: list[Detector] = [
    *duplicates.DETECTORS,
    *arithmetic.DETECTORS,
    *vat.DETECTORS,
    *contracts.DETECTORS,
    *fixed_charges.DETECTORS,
    *periods.DETECTORS,
    *consumption.DETECTORS,
    *credits.DETECTORS,
    *energy_tax.DETECTORS,
]


@dataclass
class EngineResult:
    findings: list[Finding]
    rule_versions: dict[str, str]
    errors: list[dict] = field(default_factory=list)
    duration_ms: int = 0


def run_detectors(ctx: AnalysisContext, detectors: list[Detector] | None = None) -> EngineResult:
    started = time.perf_counter()
    detectors = detectors or ALL_DETECTORS
    mark_data_duplicates(ctx)
    findings: list[Finding] = []
    errors: list[dict] = []
    for det in detectors:
        try:
            findings.extend(det.run(ctx))
        except Exception as exc:  # a broken rule must be visible, never silently skipped
            log.exception("detector %s failed", det.rule_id)
            errors.append({"rule_id": det.rule_id, "error": type(exc).__name__})
    # Materiality: drop potential errors whose (positive) value is below the threshold.
    findings = [f for f in findings if f.potential_recovery == 0
                or f.potential_recovery >= ctx.settings.min_potential_recovery]
    _annotate_credited(ctx, findings)
    return EngineResult(
        findings=findings,
        rule_versions={d.rule_id: d.version for d in detectors},
        errors=errors,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


def _annotate_credited(ctx: AnalysisContext, findings: list[Finding]) -> None:
    """Warn when the invoice of a finding has since been corrected by a credit note."""
    credits_by_ref: dict[str, list] = {}
    for c in ctx.credit_notes:
        if c.corrects_invoice_number:
            credits_by_ref.setdefault(c.corrects_invoice_number, []).append(c)
    for f in findings:
        if f.invoice is None or f.invoice.invoice_number not in credits_by_ref:
            continue
        labels = ", ".join(c.label for c in credits_by_ref[f.invoice.invoice_number])
        f.description += (f" Let op: deze factuur is gecorrigeerd met creditnota {labels}; controleer of deze "
                          "bevinding daarmee al is opgelost.")
        f.calculation.append(f"Creditnota('s) op deze factuur: {labels}")
        f.requires_verification = True
