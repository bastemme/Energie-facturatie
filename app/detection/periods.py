"""Billing periods per connection: overlaps (possible double billing) and gaps (missing invoices)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from app.detection.base import AnalysisContext, Detector, Finding, eur, invoice_field_evidence, invoice_ref
from app.domain.confidence import EvidenceQuality, confidence_from_evidence
from app.domain.enums import Classification
from app.domain.money import ZERO, format_decimal_nl, round_cents
from app.domain.periods import Period
from app.models import Invoice

OVERLAP_RULE = "billing_period_overlap"
GAP_RULE = "billing_period_gap"

NETWORK_OPERATORS = ("liander", "enexis", "stedin", "westland infra", "coteq", "rendo", "tennet")


def _is_network_operator(supplier: str | None) -> bool:
    s = (supplier or "").lower()
    return any(op in s for op in NETWORK_OPERATORS)


def connection_groups(ctx: AnalysisContext) -> dict[tuple[str, bool], list[Invoice]]:
    credited = ctx.credited_invoice_numbers()
    groups: dict[tuple[str, bool], list[Invoice]] = defaultdict(list)
    for inv in ctx.regular_invoices:
        if inv.period is None or inv.invoice_number in credited:
            continue
        key = inv.ean or (f"meter:{inv.meter_number}" if inv.meter_number else None)
        if key:
            groups[(key, _is_network_operator(inv.supplier))].append(inv)
    for invs in groups.values():
        invs.sort(key=lambda i: (i.billing_period_start, i.billing_period_end, i.invoice_number or ""))
    return groups


def _is_exact_duplicate(a: Invoice, b: Invoice) -> bool:
    return a.period == b.period and a.total_incl_vat is not None and a.total_incl_vat == b.total_incl_vat


def detect_overlaps(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for (conn, _), invs in connection_groups(ctx).items():
        for i, a in enumerate(invs):
            for b in invs[i + 1:]:
                if b.billing_period_start > a.billing_period_end:
                    break
                overlap = a.period.overlap(b.period)
                if overlap is None or _is_exact_duplicate(a, b):
                    continue  # exact duplicates are reported by the duplicate detector
                later = b
                fixed = [li for li in later.lines if li.category.is_fixed_charge and li.amount is not None]
                fixed_total = sum((li.amount for li in fixed), ZERO)
                share = Decimal(overlap.days) / Decimal(later.period.days)
                estimate = round_cents(fixed_total * share)
                suppliers = {a.supplier, b.supplier}
                multi = len(suppliers) > 1
                findings.append(Finding(
                    rule_id=OVERLAP_RULE, rule_version="1.0", category="period",
                    classification=Classification.POTENTIAL_ERROR,
                    confidence=confidence_from_evidence([EvidenceQuality.DERIVED]),
                    title=f"Overlappende factuurperiodes voor aansluiting {conn} ({a.label} / {b.label})",
                    description=(
                        f"{invoice_ref(a).capitalize()} ({a.supplier}) en {invoice_ref(b)} ({b.supplier}) overlappen "
                        f"{overlap.days} dagen ({overlap}). Voor deze dagen is mogelijk dubbel gefactureerd"
                        + (" door twee verschillende leveranciers (mogelijk overstapfout)" if multi else "")
                        + f". Voorzichtige schatting op basis van vaste kosten van {b.label}: {eur(estimate)}. "
                        "Eventueel dubbel gefactureerd verbruik is hierin niet meegenomen en moet via meterstanden "
                        "worden vastgesteld."
                    ),
                    reason="Twee facturen voor dezelfde aansluiting dekken (deels) dezelfde dagen.",
                    invoice=b, actual=Decimal(overlap.days), expected=ZERO, difference=Decimal(overlap.days),
                    unit="dagen", potential_recovery=estimate,
                    calculation=[
                        f"Overlap: {overlap} = {overlap.days} dagen",
                        f"Vaste kosten {b.label}: {eur(fixed_total)} over {later.period.days} dagen",
                        f"Schatting: {eur(fixed_total)} × {overlap.days}/{later.period.days} = {eur(estimate)}",
                    ],
                    evidence=[
                        invoice_field_evidence(ctx, a, "billing_period_start", f"Periode {a.label}: {a.period}",
                                               str(a.period)),
                        invoice_field_evidence(ctx, b, "billing_period_start", f"Periode {b.label}: {b.period}",
                                               str(b.period)),
                    ],
                    key=a.id,
                ))
    return findings


def detect_gaps(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for (conn, _), invs in connection_groups(ctx).items():
        covered_until = None
        prev = None
        for inv in invs:
            if prev is not None and covered_until is not None:
                gap = Period(prev.billing_period_start, covered_until).gap_to(inv.period)
                if gap is not None:
                    findings.append(Finding(
                        rule_id=GAP_RULE, rule_version="1.0", category="period",
                        classification=Classification.ANOMALY,
                        confidence=confidence_from_evidence([EvidenceQuality.DERIVED]),
                        title=f"Geen factuur gevonden voor {gap.days} dagen ({conn})",
                        description=(
                            f"Tussen {invoice_ref(prev)} en {invoice_ref(inv)} ontbreekt de periode {gap} "
                            f"({gap.days} dagen). Mogelijk is een factuur niet aangeleverd, of kan de leverancier "
                            "deze periode nog (na)factureren. Dit is geen bewijs van een fout."
                        ),
                        reason="Periode zonder factuur tussen twee opeenvolgende facturen.",
                        invoice=inv, actual=Decimal(gap.days), unit="dagen",
                        calculation=[f"Gedekt t/m {covered_until:%d-%m-%Y}",
                                     f"Volgende start {inv.billing_period_start:%d-%m-%Y}",
                                     f"Ontbrekend: {gap} = {format_decimal_nl(Decimal(gap.days))} dagen"],
                        key=f"gap:{gap.start.isoformat()}",
                    ))
            if covered_until is None or inv.billing_period_end > covered_until:
                covered_until = inv.billing_period_end
            prev = inv
    return findings


DETECTORS = [
    Detector(OVERLAP_RULE, "1.0", "Overlappende factuurperiodes per aansluiting", detect_overlaps),
    Detector(GAP_RULE, "1.0", "Ontbrekende factuurperiodes", detect_gaps),
]
