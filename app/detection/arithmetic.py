"""Internal arithmetic consistency of an invoice: qty × price = amount; Σ lines = subtotal; subtotal + VAT = total."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.detection.base import (
    AnalysisContext,
    Detector,
    Finding,
    charge_sign,
    eur,
    invoice_field_evidence,
    invoice_quality,
    invoice_ref,
    line_evidence,
)
from app.domain.confidence import EvidenceQuality, confidence_from_evidence
from app.domain.enums import Classification
from app.domain.money import ZERO, format_decimal_nl, format_price, round_cents

LINE_RULE = "line_arithmetic"
TOTALS_RULE = "invoice_totals"


def is_display_rounding(implied_price: Decimal, printed_price: Decimal) -> bool:
    """A tariff printed with fewer decimals than used in the calculation is not a billing error.

    True when rounding the implied price (amount / quantity) to 2–5 decimals gives exactly the printed
    tariff, e.g. printed €0,10 with implied €0,10154. Independent of how the DB stores trailing zeros.
    """
    for places in range(2, 6):
        q = Decimal(1).scaleb(-places)
        if printed_price == printed_price.quantize(q) and implied_price.quantize(q, ROUND_HALF_UP) == printed_price:
            return True
    return False


def detect_line_arithmetic(ctx: AnalysisContext) -> list[Finding]:
    tol = ctx.settings.line_amount_tolerance
    findings: list[Finding] = []
    for inv in ctx.active_invoices:
        for line in inv.lines:
            if line.quantity is None or line.unit_price is None or line.amount is None or line.quantity == 0:
                continue
            expected = round_cents(line.quantity * line.unit_price)
            diff = line.amount - expected
            if abs(diff) <= tol:
                continue
            if is_display_rounding(line.amount / line.quantity, line.unit_price):
                continue
            overcharge = diff * charge_sign(inv)
            conf = confidence_from_evidence([invoice_quality(inv, line)], extraction_confidence=line.confidence)
            findings.append(Finding(
                rule_id=LINE_RULE, rule_version="1.0", category="arithmetic",
                classification=Classification.POTENTIAL_ERROR, confidence=conf,
                title=f"Regelbedrag klopt niet met hoeveelheid × tarief ({inv.label})",
                description=(
                    f"De regel '{line.description}' op {invoice_ref(inv)} vermeldt "
                    f"{format_decimal_nl(line.quantity)} {line.unit or ''} × € {format_price(line.unit_price)} "
                    f"= {eur(expected)}, maar het gefactureerde bedrag is {eur(line.amount)}. "
                    f"Verschil: {eur(diff)}."
                ),
                reason="Hoeveelheid × tarief is niet gelijk aan het regelbedrag (buiten afrondingsmarge).",
                invoice=inv, line=line, actual=line.amount, expected=expected, difference=diff,
                potential_recovery=overcharge,
                calculation=[
                    f"{format_decimal_nl(line.quantity)} × {format_price(line.unit_price)} = "
                    f"{format_decimal_nl(line.quantity * line.unit_price)} → afgerond {eur(expected)}",
                    f"Gefactureerd: {eur(line.amount)}",
                    f"Verschil: {eur(line.amount)} − {eur(expected)} = {eur(diff)}",
                    f"Afrondingsmarge: {eur(tol)}",
                ],
                evidence=[line_evidence(line)],
            ))
    return findings


def detect_invoice_totals(ctx: AnalysisContext) -> list[Finding]:
    tol = ctx.settings.total_tolerance
    findings: list[Finding] = []
    for inv in ctx.active_invoices:
        amounts = [li.amount for li in inv.lines if li.amount is not None]
        quality = invoice_quality(inv)
        if amounts and inv.subtotal_excl_vat is not None:
            line_sum = round_cents(sum(amounts, ZERO))
            diff = inv.subtotal_excl_vat - line_sum
            if abs(diff) > tol:
                # With unverified PDF parsing a missing line is a likelier cause than a supplier error.
                qualities = [quality] if quality == EvidenceQuality.EXPLICIT else [EvidenceQuality.ESTIMATED]
                findings.append(Finding(
                    rule_id=TOTALS_RULE, rule_version="1.0", category="arithmetic",
                    classification=Classification.POTENTIAL_ERROR,
                    confidence=confidence_from_evidence(qualities, extraction_confidence=inv.extraction_confidence
                                                        if quality != EvidenceQuality.EXPLICIT else None),
                    title=f"Subtotaal wijkt af van de som van de regels ({inv.label})",
                    description=(
                        f"Op {invoice_ref(inv)} is de som van {len(amounts)} factuurregels {eur(line_sum)}, "
                        f"terwijl het subtotaal {eur(inv.subtotal_excl_vat)} is. Verschil: {eur(diff)}."
                        + ("" if quality == EvidenceQuality.EXPLICIT else
                           " Let op: de factuurregels zijn automatisch uitgelezen; controleer eerst of alle "
                           "regels herkend zijn.")
                    ),
                    reason="Som van regelbedragen ≠ subtotaal excl. btw.",
                    invoice=inv, actual=inv.subtotal_excl_vat, expected=line_sum, difference=diff,
                    potential_recovery=diff * charge_sign(inv),
                    calculation=[f"Σ regels = {' + '.join(format_decimal_nl(a, 2) for a in amounts)} = {eur(line_sum)}",
                                 f"Subtotaal factuur = {eur(inv.subtotal_excl_vat)}",
                                 f"Verschil = {eur(diff)}"],
                    evidence=[invoice_field_evidence(ctx, inv, "subtotal_excl_vat", "Subtotaal op factuur",
                                                     inv.subtotal_excl_vat)]
                    + [line_evidence(li) for li in inv.lines],
                    key="subtotal",
                ))
        if inv.subtotal_excl_vat is not None and inv.vat_amount is not None and inv.total_incl_vat is not None:
            expected_total = inv.subtotal_excl_vat + inv.vat_amount
            diff = inv.total_incl_vat - expected_total
            if abs(diff) > tol:
                findings.append(Finding(
                    rule_id=TOTALS_RULE, rule_version="1.0", category="arithmetic",
                    classification=Classification.POTENTIAL_ERROR,
                    confidence=confidence_from_evidence([quality], extraction_confidence=inv.extraction_confidence),
                    title=f"Totaalbedrag ≠ subtotaal + btw ({inv.label})",
                    description=(
                        f"Op {invoice_ref(inv)} geldt: subtotaal {eur(inv.subtotal_excl_vat)} + btw "
                        f"{eur(inv.vat_amount)} = {eur(expected_total)}, maar het totaal is "
                        f"{eur(inv.total_incl_vat)}. Verschil: {eur(diff)}."
                    ),
                    reason="Totaal incl. btw ≠ subtotaal + btw.",
                    invoice=inv, actual=inv.total_incl_vat, expected=expected_total, difference=diff,
                    potential_recovery=diff * charge_sign(inv),
                    calculation=[f"{eur(inv.subtotal_excl_vat)} + {eur(inv.vat_amount)} = {eur(expected_total)}",
                                 f"Totaal op factuur = {eur(inv.total_incl_vat)}", f"Verschil = {eur(diff)}"],
                    evidence=[
                        invoice_field_evidence(ctx, inv, "subtotal_excl_vat", "Subtotaal", inv.subtotal_excl_vat),
                        invoice_field_evidence(ctx, inv, "vat_amount", "Btw-bedrag", inv.vat_amount),
                        invoice_field_evidence(ctx, inv, "total_incl_vat", "Totaal", inv.total_incl_vat),
                    ],
                    key="total",
                ))
    return findings


DETECTORS = [
    Detector(LINE_RULE, "1.0", "Hoeveelheid × tarief = regelbedrag", detect_line_arithmetic),
    Detector(TOTALS_RULE, "1.0", "Som regels = subtotaal; subtotaal + btw = totaal", detect_invoice_totals),
]
