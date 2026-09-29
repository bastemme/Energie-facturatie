"""Energy-tax checks against admin-verified reference rates.

Legal sensitivity: rates and bracket rules change yearly. This detector:
- only runs when verified ReferenceRate rows exist for the period (none are shipped),
- states its assumption (bracket limits applied pro rata to the billing period),
- is capped at MEDIUM confidence and always requires verification,
- records the reference-rate ids it used.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
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
    rate_evidence,
)
from app.domain.confidence import EvidenceQuality, confidence_from_evidence
from app.domain.enums import Classification, Confidence, LineCategory, RateKind
from app.domain.money import ZERO, format_decimal_nl, format_price, round_cents
from app.domain.periods import Period
from app.domain.units import convert_quantity
from app.models import ReferenceRate

RULE = "energy_tax"
REDUCTION_RULE = "energy_tax_reduction_missing"
ASSUMPTION = ("Aanname: schijfgrenzen zijn tijdsevenredig toegepast op de factuurperiode "
              "(grens × dagen in periode / dagen in jaar). Fiscale verificatie vereist.")


def _brackets(rates: list[ReferenceRate], kind: RateKind, period: Period) -> list[ReferenceRate]:
    valid = [r for r in rates if r.kind == kind and r.valid_from <= period.start
             and (r.valid_to is None or period.end <= r.valid_to)]
    return sorted(valid, key=lambda r: r.bracket_from or ZERO)


def expected_tax(quantity: Decimal, period: Period, brackets: list[ReferenceRate]):
    year_days = Decimal(Period(date(period.start.year, 1, 1), date(period.start.year, 12, 31)).days)
    factor = Decimal(period.days) / year_days
    remaining = quantity
    total = ZERO
    steps = []
    for b in brackets:
        low = (b.bracket_from or ZERO) * factor
        high = b.bracket_to * factor if b.bracket_to is not None else None
        if remaining <= 0:
            break
        width = (high - low) if high is not None else remaining
        qty = min(remaining, width)
        if qty <= 0:
            continue
        part = qty * b.rate
        total += part
        remaining -= qty
        steps.append(f"Schijf {format_decimal_nl(b.bracket_from or ZERO)}–"
                     f"{format_decimal_nl(b.bracket_to) if b.bracket_to is not None else '∞'} (prorata grens "
                     f"{format_decimal_nl(low.quantize(Decimal('0.01')))}–"
                     f"{format_decimal_nl(high.quantize(Decimal('0.01'))) if high is not None else '∞'}): "
                     f"{format_decimal_nl(qty.quantize(Decimal('0.0001')))} × € {format_price(b.rate)} = "
                     f"€ {format_price(part.quantize(Decimal('0.0001')))}")
    if remaining > 0:
        return None
    return round_cents(total), steps


def detect_energy_tax(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for inv in ctx.active_invoices:
        tax_lines = [li for li in inv.lines if li.category.is_energy_tax and li.amount is not None
                     and li.quantity is not None]
        if not tax_lines:
            continue
        # Several tax lines (one per bracket) → evaluate their combined quantity and amount.
        by_kind: dict[LineCategory, list] = defaultdict(list)
        for li in tax_lines:
            by_kind[li.category].append(li)
        for category, lines in by_kind.items():
            period = lines[0].period
            if period is None or period.start.year != period.end.year:
                continue
            kind = (RateKind.ENERGY_TAX_GAS if category == LineCategory.ENERGY_TAX_GAS
                    else RateKind.ENERGY_TAX_ELECTRICITY)
            brackets = _brackets(ctx.reference_rates, kind, period)
            if not brackets:
                continue
            unit = brackets[0].unit
            qty = ZERO
            for li in lines:
                q = convert_quantity(li.quantity, li.unit, unit)
                if q is None:
                    break
                qty += q
            else:
                result = expected_tax(qty, period, brackets)
                if result is None:
                    continue
                expected, steps = result
                actual = sum((li.amount for li in lines), ZERO)
                diff = actual - expected
                if abs(diff) <= ctx.settings.line_amount_tolerance * len(lines):
                    continue
                findings.append(Finding(
                    rule_id=RULE, rule_version="1.0", category="tax",
                    classification=Classification.POTENTIAL_ERROR,
                    confidence=confidence_from_evidence([invoice_quality(inv, lines[0]), EvidenceQuality.DERIVED],
                                                        cap=Confidence.MEDIUM),
                    title=f"Energiebelasting wijkt af van referentietarieven ({inv.label})",
                    description=(
                        f"Op {invoice_ref(inv)} is {eur(actual)} energiebelasting berekend over "
                        f"{format_decimal_nl(qty)} {unit}. Op basis van de geverifieerde referentietarieven komt dit "
                        f"uit op {eur(expected)}. Mogelijke discrepantie: {eur(diff)}. {ASSUMPTION}"
                    ),
                    reason="Energiebelasting ≠ berekening met geverifieerde schijftarieven (tarief-ids vastgelegd).",
                    invoice=inv, line=lines[0], actual=actual, expected=expected, difference=diff,
                    potential_recovery=diff * charge_sign(inv),
                    calculation=[*steps, f"Verwacht: {eur(expected)}", f"Gefactureerd: {eur(actual)}",
                                 f"Verschil: {eur(diff)}", ASSUMPTION,
                                 "Gebruikte tarief-ids: " + ", ".join(b.id for b in brackets)],
                    evidence=[line_evidence(li) for li in lines] + [rate_evidence(b) for b in brackets],
                    requires_verification=True, key=category.value,
                ))
    return findings


def detect_missing_tax_reduction(ctx: AnalysisContext) -> list[Finding]:
    """Electricity connection billed for (part of) a year without any 'vermindering energiebelasting'."""
    per_year: dict[tuple[str, int], list] = defaultdict(list)
    for inv in ctx.regular_invoices:
        if not inv.ean or inv.period is None:
            continue
        has_elec = any(li.category in (LineCategory.ELECTRICITY_NORMAL, LineCategory.ELECTRICITY_LOW,
                                       LineCategory.ELECTRICITY_SINGLE) for li in inv.lines)
        has_tax = any(li.category == LineCategory.ENERGY_TAX_ELECTRICITY for li in inv.lines)
        if has_elec and has_tax:
            per_year[(inv.ean, inv.billing_period_start.year)].append(inv)
    findings: list[Finding] = []
    for (ean, year), invs in per_year.items():
        days = sum(i.period.days for i in invs)
        if days < 28:
            continue
        if any(li.category == LineCategory.TAX_REDUCTION for i in invs for li in i.lines):
            continue
        invs.sort(key=lambda i: i.billing_period_start)
        findings.append(Finding(
            rule_id=REDUCTION_RULE, rule_version="1.0", category="tax", classification=Classification.ANOMALY,
            confidence=Confidence.LOW,
            title=f"Geen vermindering energiebelasting gevonden ({ean}, {year})",
            description=(
                f"Op {len(invs)} factuur/facturen voor aansluiting {ean} in {year} is geen vermindering "
                "energiebelasting herkend. Of deze aansluiting daarvoor in aanmerking komt, hangt af van o.a. het "
                "gebruik van het pand en de geldende wetgeving in dat jaar. Fiscale verificatie vereist; geen bedrag "
                "berekend."
            ),
            reason="Geen belastingvermindering op facturen met energiebelasting elektriciteit.",
            invoice=invs[0], requires_verification=True, key=f"{ean}:{year}",
        ))
    return findings


DETECTORS = [
    Detector(RULE, "1.0", "Energiebelasting vs geverifieerde schijftarieven", detect_energy_tax),
    Detector(REDUCTION_RULE, "1.0", "Ontbrekende vermindering energiebelasting", detect_missing_tax_reduction),
]
