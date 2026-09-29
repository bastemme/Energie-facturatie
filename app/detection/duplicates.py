"""Duplicate invoices and duplicate line items."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC

from app.detection.base import (
    STRUCTURED_METHODS,
    AnalysisContext,
    Detector,
    Finding,
    eur,
    invoice_field_evidence,
    invoice_ref,
    line_evidence,
    upper_first,
)
from app.domain.confidence import EvidenceQuality, confidence_from_evidence
from app.domain.enums import Classification, Confidence
from app.domain.money import ZERO
from app.models import Invoice

DATA_DUP_RULE = "duplicate_invoice_number"
BILLED_TWICE_RULE = "duplicate_invoice_content"
LINE_RULE = "duplicate_line"


def _supplier_key(inv: Invoice) -> str:
    return (inv.supplier or "").strip().lower()


def _preference(inv: Invoice) -> tuple:
    structured = bool(inv.lines) and all(li.extraction_method in STRUCTURED_METHODS for li in inv.lines)
    # SQLite returns naive datetimes while freshly created rows still hold aware ones: compare as UTC timestamps.
    created = inv.created_at
    ts = (created if created.tzinfo else created.replace(tzinfo=UTC)).timestamp() if created else 0
    return (inv.is_verified, structured, inv.extraction_confidence or 0, ts)


def mark_data_duplicates(ctx: AnalysisContext) -> list[tuple[Invoice, list[Invoice]]]:
    """Same supplier + invoice number + total = the same invoice delivered twice (e.g. PDF and Excel).

    These are data duplicates, not billing errors: keep the best-sourced copy and exclude the others
    from all further analysis so nothing is counted twice.
    """
    groups: dict[tuple[str, str], list[Invoice]] = defaultdict(list)
    for inv in ctx.invoices:
        if inv.invoice_number:
            groups[(_supplier_key(inv), inv.invoice_number)].append(inv)
    pairs = []
    for invs in groups.values():
        if len(invs) < 2:
            continue
        totals = {i.total_incl_vat for i in invs if i.total_incl_vat is not None}
        if len(totals) <= 1:
            keep = max(invs, key=_preference)
            dups = [i for i in invs if i is not keep]
            ctx.duplicate_invoice_ids.update(i.id for i in dups)
            pairs.append((keep, dups))
    return pairs


def detect_duplicate_numbers(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    groups: dict[tuple[str, str], list[Invoice]] = defaultdict(list)
    for inv in ctx.invoices:
        if inv.invoice_number:
            groups[(_supplier_key(inv), inv.invoice_number)].append(inv)
    for (_, number), invs in groups.items():
        if len(invs) < 2:
            continue
        totals = {i.total_incl_vat for i in invs if i.total_incl_vat is not None}
        same = len(totals) <= 1
        findings.append(Finding(
            rule_id=DATA_DUP_RULE, rule_version="1.0", category="duplicate",
            classification=Classification.ANOMALY,
            confidence=Confidence.HIGH if same else Confidence.MEDIUM,
            title=(f"Factuur {number} is {len(invs)}× aangeleverd" if same
                   else f"Factuurnummer {number} komt voor met verschillende bedragen"),
            description=(
                f"Factuurnummer {number} komt {len(invs)} keer voor in de aangeleverde documenten"
                + (" met hetzelfde totaalbedrag. Dit is waarschijnlijk hetzelfde document in twee formaten; "
                   "alleen één exemplaar is meegenomen in de analyse." if same else
                   f" met verschillende totaalbedragen ({', '.join(eur(t) for t in sorted(totals))}). Mogelijk "
                   "een gecorrigeerde versie of een uitleesfout — controleer welke versie geldig is.")
            ),
            reason="Zelfde leverancier en factuurnummer meerdere keren aanwezig.",
            invoice=invs[0], potential_recovery=ZERO,
            evidence=[invoice_field_evidence(ctx, i, "invoice_number", "Factuurnummer in document", number)
                      for i in invs],
            key=number,
        ))
    return findings


def detect_billed_twice(ctx: AnalysisContext) -> list[Finding]:
    """Different invoice numbers, same connection + period + total → possibly billed twice."""
    credited = ctx.credited_invoice_numbers()
    findings: list[Finding] = []
    groups: dict[tuple, list[Invoice]] = defaultdict(list)
    for inv in ctx.regular_invoices:
        if inv.period is None or not inv.ean or inv.total_incl_vat in (None, ZERO) or inv.invoice_number in credited:
            continue
        groups[(inv.ean, inv.billing_period_start, inv.billing_period_end, inv.total_incl_vat)].append(inv)
    for invs in groups.values():
        if len(invs) < 2:
            continue
        invs.sort(key=lambda i: (i.invoice_date or i.billing_period_end, i.invoice_number or ""))
        original = invs[0]
        for dup in invs[1:]:
            recovery = dup.subtotal_excl_vat if dup.subtotal_excl_vat is not None else ZERO
            findings.append(Finding(
                rule_id=BILLED_TWICE_RULE, rule_version="1.0", category="duplicate",
                classification=Classification.POTENTIAL_ERROR,
                confidence=confidence_from_evidence([EvidenceQuality.DERIVED]),
                title=f"Mogelijk dubbel gefactureerd: {original.label} en {dup.label}",
                description=(
                    f"{upper_first(invoice_ref(dup))} heeft dezelfde aansluiting ({dup.ean}), dezelfde periode en "
                    f"hetzelfde totaalbedrag ({eur(dup.total_incl_vat)}) als {invoice_ref(original)}, maar een ander "
                    f"factuurnummer. Mogelijke discrepantie: {eur(recovery)} excl. btw. Verificatie vereist: "
                    "controleer of beide facturen zijn betaald en of er geen creditnota tegenover staat."
                ),
                reason="Zelfde EAN, periode en totaalbedrag onder verschillende factuurnummers.",
                invoice=dup, actual=dup.total_incl_vat, expected=ZERO, difference=dup.total_incl_vat,
                potential_recovery=recovery,
                calculation=[f"{original.label}: {eur(original.total_incl_vat)} incl. btw, periode {original.period}",
                             f"{dup.label}: {eur(dup.total_incl_vat)} incl. btw, periode {dup.period}",
                             f"Mogelijk terug te vorderen (excl. btw): {eur(recovery)}"],
                evidence=[invoice_field_evidence(ctx, original, "total_incl_vat", f"Totaal {original.label}",
                                                 original.total_incl_vat),
                          invoice_field_evidence(ctx, dup, "total_incl_vat", f"Totaal {dup.label}",
                                                 dup.total_incl_vat)],
                key=original.id,
            ))
    return findings


def detect_duplicate_lines(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for inv in ctx.active_invoices:
        seen: dict[tuple, object] = {}
        for line in inv.lines:
            if line.amount is None or line.amount == ZERO:
                continue
            key = (line.description.strip().lower(), line.quantity, line.unit_price, line.amount,
                   line.period_start, line.period_end)
            if key not in seen:
                seen[key] = line
                continue
            first = seen[key]
            findings.append(Finding(
                rule_id=LINE_RULE, rule_version="1.0",
                category="fixed_charge" if line.category.is_fixed_charge else "duplicate",
                classification=Classification.POTENTIAL_ERROR,
                confidence=confidence_from_evidence([EvidenceQuality.DERIVED]),
                title=f"Dubbele factuurregel '{line.description}' ({inv.label})",
                description=(
                    f"Op {invoice_ref(inv)} staat de regel '{line.description}' ({eur(line.amount)}) twee keer met "
                    "identieke hoeveelheid, tarief en bedrag. Mogelijk dubbel in rekening gebracht; het kan ook om "
                    f"twee aansluitingen gaan. Mogelijke discrepantie: {eur(line.amount)}."
                ),
                reason="Identieke regel komt twee keer voor op dezelfde factuur.",
                invoice=inv, line=line, actual=line.amount * 2, expected=line.amount, difference=line.amount,
                potential_recovery=line.amount,
                calculation=[f"Regel 1: {eur(first.amount)}", f"Regel 2: {eur(line.amount)}",
                             f"Mogelijk dubbel: {eur(line.amount)}"],
                evidence=[line_evidence(first, "Eerste regel"), line_evidence(line, "Tweede, identieke regel")],
            ))
    return findings


DETECTORS = [
    Detector(DATA_DUP_RULE, "1.0", "Zelfde factuurnummer meerdere keren", detect_duplicate_numbers),
    Detector(BILLED_TWICE_RULE, "1.0", "Zelfde EAN + periode + bedrag, ander nummer", detect_billed_twice),
    Detector(LINE_RULE, "1.0", "Dubbele factuurregels", detect_duplicate_lines),
]
