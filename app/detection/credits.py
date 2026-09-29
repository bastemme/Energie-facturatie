"""Credit notes and corrections: do they reconcile with the original and the rebill?"""

from __future__ import annotations

from app.detection.base import AnalysisContext, Detector, Finding, eur, invoice_field_evidence, invoice_ref
from app.domain.confidence import EvidenceQuality, confidence_from_evidence
from app.domain.enums import Classification
from app.models import Invoice

RULE = "credit_reconciliation"


def _abs_subtotal(inv: Invoice):
    return abs(inv.subtotal_excl_vat) if inv.subtotal_excl_vat is not None else None


def detect_credit_reconciliation(ctx: AnalysisContext) -> list[Finding]:
    tol = ctx.settings.total_tolerance
    by_number = {i.invoice_number: i for i in ctx.regular_invoices if i.invoice_number}
    findings: list[Finding] = []
    for credit in ctx.credit_notes:
        ref = credit.corrects_invoice_number
        credit_amt = _abs_subtotal(credit)
        if not ref:
            findings.append(Finding(
                rule_id=RULE, rule_version="1.0", category="credit", classification=Classification.ANOMALY,
                confidence=confidence_from_evidence([EvidenceQuality.DERIVED]),
                title=f"Creditnota {credit.label} zonder verwijzing naar originele factuur",
                description=(f"Creditnota {credit.label} ({eur(credit_amt)} excl. btw) verwijst niet naar een "
                             "factuurnummer. Koppel de creditnota handmatig om te kunnen controleren of de "
                             "correctie volledig is."),
                reason="Creditnota zonder herleidbare originele factuur.", invoice=credit, key="noref",
            ))
            continue
        original = by_number.get(ref)
        if original is None:
            findings.append(Finding(
                rule_id=RULE, rule_version="1.0", category="credit", classification=Classification.ANOMALY,
                confidence=confidence_from_evidence([EvidenceQuality.DERIVED]),
                title=f"Originele factuur {ref} van creditnota {credit.label} ontbreekt",
                description=(f"Creditnota {credit.label} corrigeert factuur {ref}, maar die factuur is niet "
                             "aangeleverd. Vraag deze op om de correctie te kunnen controleren."),
                reason="Originele factuur ontbreekt.", invoice=credit, key="missing",
                evidence=[invoice_field_evidence(ctx, credit, "corrects_invoice_number", "Verwijzing op creditnota",
                                                 ref)],
            ))
            continue
        orig_amt = _abs_subtotal(original)
        if orig_amt is None or credit_amt is None:
            continue
        rebills = [i for i in ctx.regular_invoices
                   if i is not original and i.ean == original.ean and i.period == original.period
                   and (i.corrects_invoice_number == ref or (i.invoice_date and credit.invoice_date
                                                             and i.invoice_date >= credit.invoice_date))]
        if rebills:
            # Full reversal + rebill expected: credit should equal the original subtotal.
            shortfall = orig_amt - credit_amt
            if shortfall > tol:
                rebill = rebills[0]
                findings.append(Finding(
                    rule_id=RULE, rule_version="1.0", category="credit",
                    classification=Classification.POTENTIAL_ERROR,
                    confidence=confidence_from_evidence([EvidenceQuality.DERIVED]),
                    title=f"Creditnota {credit.label} crediteert factuur {ref} niet volledig",
                    description=(
                        f"Factuur {ref} ({eur(orig_amt)} excl. btw) is gecrediteerd met {credit.label} voor "
                        f"{eur(credit_amt)} en opnieuw gefactureerd met {invoice_ref(rebill)}. Bij een volledige "
                        f"herfacturering wordt een volledige creditering verwacht. Mogelijk dubbel betaald: "
                        f"{eur(shortfall)}."
                    ),
                    reason="Creditering + herfacturering sluit niet aan op de originele factuur.",
                    invoice=credit, actual=credit_amt, expected=orig_amt, difference=credit_amt - orig_amt,
                    potential_recovery=shortfall,
                    calculation=[f"Origineel {ref}: {eur(orig_amt)}", f"Gecrediteerd: {eur(credit_amt)}",
                                 f"Herfactuur {rebill.label}: {eur(_abs_subtotal(rebill))}",
                                 f"Niet gecrediteerd deel: {eur(shortfall)}"],
                    evidence=[
                        invoice_field_evidence(ctx, original, "subtotal_excl_vat", f"Subtotaal {ref}", orig_amt),
                        invoice_field_evidence(ctx, credit, "subtotal_excl_vat", f"Subtotaal {credit.label}",
                                               credit_amt),
                        invoice_field_evidence(ctx, rebill, "subtotal_excl_vat", f"Subtotaal {rebill.label}",
                                               _abs_subtotal(rebill)),
                    ],
                    key="partial",
                ))
        elif credit_amt - orig_amt > tol:
            findings.append(Finding(
                rule_id=RULE, rule_version="1.0", category="credit", classification=Classification.ANOMALY,
                confidence=confidence_from_evidence([EvidenceQuality.DERIVED]),
                title=f"Creditnota {credit.label} is hoger dan de originele factuur {ref}",
                description=(f"Creditnota {credit.label} ({eur(credit_amt)}) overtreft factuur {ref} "
                             f"({eur(orig_amt)}). Controleer of een herfactuur ontbreekt."),
                reason="Creditbedrag > origineel bedrag zonder herfacturering.", invoice=credit, key="over",
            ))
    return findings


DETECTORS = [Detector(RULE, "1.0", "Aansluiting creditnota's op origineel en herfactuur",
                      detect_credit_reconciliation)]
