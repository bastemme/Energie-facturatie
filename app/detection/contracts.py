"""Invoice vs contract: tariffs, fixed charges, and charges outside the contract period."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.detection.base import (
    AnalysisContext,
    Detector,
    Finding,
    charge_sign,
    contract_price_evidence,
    eur,
    invoice_quality,
    invoice_ref,
    line_evidence,
)
from app.domain.confidence import EvidenceQuality, confidence_from_evidence
from app.domain.enums import Classification, Commodity, LineCategory
from app.domain.money import ZERO, format_decimal_nl, round_cents
from app.domain.periods import Period, months_between
from app.domain.units import convert_quantity, normalize_unit
from app.models import Contract, ContractPrice, Invoice, InvoiceLine

PRICE_RULE = "contract_price"
PERIOD_RULE = "contract_period"

_TIME_UNITS = {"day", "month", "year"}
_ELECTRICITY_CATS = {LineCategory.ELECTRICITY_NORMAL, LineCategory.ELECTRICITY_LOW, LineCategory.ELECTRICITY_SINGLE,
                     LineCategory.ELECTRICITY_FEED_IN}


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def supplier_matches(contract: Contract, invoice: Invoice) -> bool:
    a, b = _norm(contract.supplier), _norm(invoice.supplier)
    return bool(a and b and (a in b or b in a))


def contract_applies(contract: Contract, invoice: Invoice, line: InvoiceLine | None = None) -> bool:
    if contract.client_id != invoice.client_id or not supplier_matches(contract, invoice):
        return False
    if contract.ean and invoice.ean and contract.ean != invoice.ean:
        return False
    if line is not None and contract.commodity != Commodity.MIXED:
        if line.category in _ELECTRICITY_CATS and contract.commodity == Commodity.GAS:
            return False
        if line.category == LineCategory.GAS_SUPPLY and contract.commodity == Commodity.ELECTRICITY:
            return False
    period = (line.period if line else None) or invoice.period
    return period is not None and period.overlap(contract.period) is not None


def _time_quantity(unit: str, period: Period) -> Decimal:
    if unit == "day":
        return Decimal(period.days)
    if unit == "month":
        return months_between(period)
    # year: prorate by days in the covered calendar years
    total = ZERO
    for year in range(period.start.year, period.end.year + 1):
        yp = Period(date(year, 1, 1), date(year, 12, 31))
        total += Decimal(period.overlap_days(yp)) / Decimal(yp.days)
    return total


def expected_for_line(line: InvoiceLine, prices: list[ContractPrice], period: Period):
    """Expected amount for a line from contract price rows, prorated by days across price periods.

    Returns (expected_amount, steps, used_prices, derived) or None if not computable.
    """
    relevant = [p for p in prices if p.period.overlap(period)]
    if not relevant:
        return None
    covered = sum(period.overlap_days(p.period) for p in relevant)
    if covered < period.days:
        return None  # part of the period has no agreed price → cannot compute
    unit = normalize_unit(line.unit)
    steps: list[str] = []
    total = ZERO
    derived = len(relevant) > 1
    for p in relevant:
        seg = period.overlap(p.period)
        share = Decimal(seg.days) / Decimal(period.days)
        p_unit = normalize_unit(p.unit)
        if p_unit in _TIME_UNITS:
            qty = _time_quantity(p_unit, seg)
            if unit in _TIME_UNITS and unit == p_unit and line.quantity is not None and len(relevant) == 1:
                qty = line.quantity  # same time unit on invoice: compare tariff, quantity checked separately
            else:
                derived = True
            part = qty * p.price
            steps.append(f"{seg}: {format_decimal_nl(qty.quantize(Decimal('0.0001')))} {p_unit} × "
                         f"€ {format_decimal_nl(p.price)} = € {format_decimal_nl(part.quantize(Decimal('0.0001')))}")
        else:
            if line.quantity is None:
                return None
            qty = convert_quantity(line.quantity, unit, p_unit)
            if qty is None:
                return None
            qty = qty * share
            part = qty * p.price
            share_txt = "" if share == 1 else f" (aandeel {seg.days}/{period.days} dagen)"
            steps.append(f"{seg}: {format_decimal_nl(qty.quantize(Decimal('0.0001')))} {p_unit}{share_txt} × "
                         f"€ {format_decimal_nl(p.price)} = € {format_decimal_nl(part.quantize(Decimal('0.0001')))}")
        total += part
    return round_cents(total), steps, relevant, derived


def detect_contract_prices(ctx: AnalysisContext) -> list[Finding]:
    tol = ctx.settings.line_amount_tolerance
    findings: list[Finding] = []
    for inv in ctx.active_invoices:
        for line in inv.lines:
            if line.amount is None or not (line.category.is_consumption or line.category.is_fixed_charge
                                           or line.category == LineCategory.ELECTRICITY_FEED_IN):
                continue
            period = line.period
            if period is None:
                continue
            for contract in ctx.contracts:
                if not contract_applies(contract, inv, line):
                    continue
                prices = [p for p in contract.prices if p.category == line.category]
                if not prices:
                    continue
                result = expected_for_line(line, prices, period)
                if result is None:
                    continue
                expected, steps, used, derived = result
                actual = line.amount
                if line.category == LineCategory.ELECTRICITY_FEED_IN:
                    # Feed-in is compensation to the client and is printed with either sign. Compare magnitudes:
                    # receiving less than agreed is money owed to the client.
                    expected = -abs(expected) if actual < 0 else abs(expected)
                    diff = actual - expected
                    recovery = abs(expected) - abs(actual)
                else:
                    diff = actual - expected
                    recovery = diff * charge_sign(inv)
                if abs(diff) <= tol:
                    continue
                qualities = [invoice_quality(inv, line),
                             EvidenceQuality.EXPLICIT if contract.verified_at else EvidenceQuality.DERIVED]
                if derived:
                    qualities.append(EvidenceQuality.DERIVED)
                conf = confidence_from_evidence(qualities, extraction_confidence=line.confidence)
                price_txt = ", ".join(f"€ {format_decimal_nl(p.price)}/{p.unit}" for p in used)
                invoiced_txt = (f"{format_decimal_nl(line.quantity)} {line.unit or ''} × € "
                                f"{format_decimal_nl(line.unit_price)} = " if line.unit_price is not None
                                and line.quantity is not None else "")
                findings.append(Finding(
                    rule_id=PRICE_RULE, rule_version="1.0",
                    category="fixed_charge" if line.category.is_fixed_charge else "price",
                    classification=Classification.POTENTIAL_ERROR, confidence=conf,
                    title=(f"{'Vaste kosten' if line.category.is_fixed_charge else 'Tarief'} wijkt af van contract "
                           f"({inv.label}, {line.description})"),
                    description=(
                        f"Op {invoice_ref(inv)} is voor '{line.description}' {invoiced_txt}{eur(actual)} "
                        f"gefactureerd. Volgens contract {contract.contract_reference or contract.supplier} geldt "
                        f"{price_txt}, wat neerkomt op {eur(expected)}. Mogelijke discrepantie: {eur(diff)}."
                        + ("" if contract.verified_at else " De contractprijzen zijn nog niet geverifieerd.")
                    ),
                    reason="Gefactureerd bedrag ≠ bedrag volgens contracttarief voor dezelfde periode.",
                    invoice=inv, line=line, actual=actual, expected=expected, difference=diff,
                    potential_recovery=recovery,
                    calculation=[*steps, f"Verwacht (afgerond): {eur(expected)}", f"Gefactureerd: {eur(actual)}",
                                 f"Verschil: {eur(diff)}"],
                    evidence=[line_evidence(line)] + [contract_price_evidence(p) for p in used],
                    key=contract.id,
                ))
                break  # one applicable contract per line
    return findings


def detect_outside_contract_period(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for inv in ctx.regular_invoices:
        period = inv.period
        if period is None:
            continue
        supplier_contracts = [c for c in ctx.contracts if supplier_matches(c, inv)
                              and (not c.ean or not inv.ean or c.ean == inv.ean)]
        if not supplier_contracts:
            continue
        covered = sum(period.overlap_days(c.period) for c in supplier_contracts)
        uncovered = period.days - min(covered, period.days)
        if uncovered <= 0:
            continue
        findings.append(Finding(
            rule_id=PERIOD_RULE, rule_version="1.0", category="contract",
            classification=Classification.ANOMALY,
            confidence=confidence_from_evidence([EvidenceQuality.DERIVED]),
            title=f"Factuurperiode valt (deels) buiten de contractperiode ({inv.label})",
            description=(
                f"{invoice_ref(inv).capitalize()} beslaat {period.days} dagen, waarvan {uncovered} dagen niet "
                f"binnen een vastgelegd contract met {inv.supplier} vallen. Controleer welk tarief voor die dagen "
                "is toegepast (bijv. variabel tarief na afloop van het contract) en of er een verlenging is."
            ),
            reason="Leveringsperiode zonder vastgelegd contract.",
            invoice=inv, calculation=[f"Periode: {period} ({period.days} dagen)",
                                      f"Dagen binnen contract: {period.days - uncovered}"],
            evidence=[], key="outside",
        ))
    return findings


DETECTORS = [
    Detector(PRICE_RULE, "1.0", "Factuurtarief vs contracttarief (incl. vaste kosten, prorata)",
             detect_contract_prices),
    Detector(PERIOD_RULE, "1.0", "Factuur buiten contractperiode", detect_outside_contract_period),
]
