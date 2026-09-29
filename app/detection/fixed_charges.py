"""Fixed charges: billed number of months/days vs the billing period."""

from __future__ import annotations

from decimal import Decimal

from app.detection.base import (
    AnalysisContext,
    Detector,
    Finding,
    charge_sign,
    eur,
    invoice_quality,
    invoice_ref,
    line_evidence,
)
from app.domain.confidence import EvidenceQuality, confidence_from_evidence
from app.domain.enums import Classification
from app.domain.money import format_decimal_nl, round_cents
from app.domain.periods import months_between
from app.domain.units import normalize_unit

RULE = "fixed_charge_period"
MONTH_TOLERANCE = Decimal("0.05")


def detect_fixed_charge_quantity(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for inv in ctx.active_invoices:
        for line in inv.lines:
            if not line.category.is_fixed_charge or line.quantity is None or line.unit_price is None:
                continue
            period = line.period
            unit = normalize_unit(line.unit)
            if period is None or unit not in ("month", "day"):
                continue
            expected_qty = months_between(period) if unit == "month" else Decimal(period.days)
            tolerance = MONTH_TOLERANCE if unit == "month" else Decimal("0.5")
            excess = line.quantity - expected_qty
            if abs(excess) <= tolerance:
                continue
            expected_amount = round_cents(expected_qty * line.unit_price)
            diff = (line.amount or round_cents(line.quantity * line.unit_price)) - expected_amount
            if abs(diff) <= ctx.settings.line_amount_tolerance:
                continue
            unit_nl = "maanden" if unit == "month" else "dagen"
            findings.append(Finding(
                rule_id=RULE, rule_version="1.0", category="fixed_charge",
                classification=Classification.POTENTIAL_ERROR,
                confidence=confidence_from_evidence([invoice_quality(inv, line), EvidenceQuality.DERIVED],
                                                    extraction_confidence=line.confidence),
                title=f"Vaste kosten voor meer {unit_nl} dan de factuurperiode ({inv.label})",
                description=(
                    f"'{line.description}' op {invoice_ref(inv)} rekent {format_decimal_nl(line.quantity)} {unit_nl}, "
                    f"terwijl de periode {period} ({period.days} dagen) overeenkomt met "
                    f"{format_decimal_nl(expected_qty.quantize(Decimal('0.01')))} {unit_nl}. "
                    f"Mogelijke discrepantie: {eur(diff)}."
                ),
                reason="Aantal gefactureerde tijdseenheden ≠ lengte van de factuurperiode.",
                invoice=inv, line=line, actual=line.amount, expected=expected_amount, difference=diff,
                potential_recovery=diff * charge_sign(inv),
                calculation=[
                    f"Periode {period}: {format_decimal_nl(expected_qty.quantize(Decimal('0.0001')))} {unit_nl}",
                    f"Verwacht: {format_decimal_nl(expected_qty.quantize(Decimal('0.0001')))} × € "
                    f"{format_decimal_nl(line.unit_price)} = {eur(expected_amount)}",
                    f"Gefactureerd: {eur(line.amount)}", f"Verschil: {eur(diff)}",
                ],
                evidence=[line_evidence(line)],
            ))
    return findings


DETECTORS = [Detector(RULE, "1.0", "Vaste kosten vs lengte factuurperiode", detect_fixed_charge_quantity)]
