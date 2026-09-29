"""VAT checks. Tax rules are legally sensitive: findings are capped at MEDIUM and always need verification."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.detection.base import (
    AnalysisContext,
    Detector,
    Finding,
    charge_sign,
    eur,
    invoice_field_evidence,
    invoice_quality,
    invoice_ref,
    rate_evidence,
)
from app.domain.confidence import EvidenceQuality, confidence_from_evidence
from app.domain.enums import Classification, Confidence, RateKind
from app.domain.money import ZERO, format_decimal_nl, percentage_of
from app.models import ReferenceRate

RULE = "vat"
VAT_DEDUCTIBLE_NOTE = (
    " Omdat deze klant btw verrekent (voorbelasting), heeft een btw-verschil naar verwachting geen netto "
    "financieel effect; de potentiële terugvordering is daarom op € 0,00 gezet."
)


def applicable_rate(rates: list[ReferenceRate], kind: RateKind, on: date) -> ReferenceRate | None:
    candidates = [r for r in rates
                  if r.kind == kind and r.valid_from <= on and (r.valid_to is None or on <= r.valid_to)]
    return max(candidates, key=lambda r: r.valid_from) if candidates else None


def detect_vat(ctx: AnalysisContext) -> list[Finding]:
    tol = ctx.settings.total_tolerance
    findings: list[Finding] = []
    for inv in ctx.active_invoices:
        if inv.subtotal_excl_vat is None or inv.vat_amount is None:
            continue
        quality = invoice_quality(inv)
        on = inv.billing_period_end or inv.invoice_date
        ref = applicable_rate(ctx.reference_rates, RateKind.VAT, on) if on else None
        stated = inv.vat_rate

        # 1) VAT amount consistent with stated rate
        if stated is not None:
            expected = percentage_of(inv.subtotal_excl_vat, stated)
            diff = inv.vat_amount - expected
            if abs(diff) > tol:
                findings.append(_finding(
                    ctx, inv, quality, expected, diff, key="amount",
                    title=f"Btw-bedrag klopt niet met vermeld btw-percentage ({inv.label})",
                    description=(f"Op {invoice_ref(inv)} is {format_decimal_nl(stated)}% btw over "
                                 f"{eur(inv.subtotal_excl_vat)} gelijk aan {eur(expected)}, maar er is "
                                 f"{eur(inv.vat_amount)} btw berekend. Verschil: {eur(diff)}."),
                    reason="Btw-bedrag ≠ subtotaal × vermeld btw-percentage.",
                    calc=[f"{eur(inv.subtotal_excl_vat)} × {format_decimal_nl(stated)}% = {eur(expected)}",
                          f"Btw op factuur = {eur(inv.vat_amount)}", f"Verschil = {eur(diff)}"],
                    extra_evidence=[],
                ))

        # 2) Stated / effective rate vs verified reference rate (only when an admin entered one)
        if ref is not None:
            effective = stated
            if effective is None and inv.subtotal_excl_vat:
                effective = (inv.vat_amount / inv.subtotal_excl_vat * Decimal(100)).quantize(Decimal("0.01"))
            if effective is not None and abs(effective - ref.rate) > Decimal("0.01"):
                expected = percentage_of(inv.subtotal_excl_vat, ref.rate)
                diff = inv.vat_amount - expected
                if abs(diff) > tol:
                    findings.append(_finding(
                        ctx, inv, quality, expected, diff, key="rate",
                        title=f"Btw-percentage wijkt af van referentietarief ({inv.label})",
                        description=(
                            f"Op {invoice_ref(inv)} is {format_decimal_nl(effective)}% btw toegepast. Het "
                            f"geverifieerde referentietarief per {on:%d-%m-%Y} is {format_decimal_nl(ref.rate)}% "
                            f"(bron: {ref.source_reference}). Bij dat tarief zou de btw {eur(expected)} zijn. "
                            f"Verschil: {eur(diff)}. Mogelijk is een deel van de factuur anders belast; "
                            "fiscale verificatie vereist."),
                        reason="Toegepast btw-percentage ≠ geverifieerd referentietarief (versie/datum vastgelegd).",
                        calc=[f"{eur(inv.subtotal_excl_vat)} × {format_decimal_nl(ref.rate)}% = {eur(expected)}",
                              f"Btw op factuur = {eur(inv.vat_amount)}", f"Verschil = {eur(diff)}",
                              f"Referentietarief-id {ref.id}, geldig vanaf {ref.valid_from:%d-%m-%Y}"],
                        extra_evidence=[rate_evidence(ref)],
                    ))
    return findings


def _finding(ctx, inv, quality, expected, diff, *, key, title, description, reason, calc, extra_evidence) -> Finding:
    recovery = ZERO if ctx.settings.vat_deductible else diff * charge_sign(inv)
    if ctx.settings.vat_deductible:
        description += VAT_DEDUCTIBLE_NOTE
    return Finding(
        rule_id=RULE, rule_version="1.0", category="tax",
        classification=Classification.POTENTIAL_ERROR,
        confidence=confidence_from_evidence([quality, EvidenceQuality.DERIVED],
                                            extraction_confidence=inv.extraction_confidence, cap=Confidence.MEDIUM),
        title=title, description=description, reason=reason, invoice=inv,
        actual=inv.vat_amount, expected=expected, difference=diff, potential_recovery=recovery,
        calculation=calc,
        evidence=[invoice_field_evidence(ctx, inv, "vat_amount", "Btw-bedrag", inv.vat_amount),
                  invoice_field_evidence(ctx, inv, "subtotal_excl_vat", "Subtotaal", inv.subtotal_excl_vat)]
        + extra_evidence,
        requires_verification=True, key=key,
    )


DETECTORS = [Detector(RULE, "1.0", "Btw-bedrag en btw-tarief", detect_vat)]
