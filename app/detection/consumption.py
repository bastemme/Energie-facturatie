"""Consumption checks: invoiced volume vs meter readings, reading continuity, estimated readings, trends."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import timedelta
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
    reading_evidence,
    upper_first,
)
from app.domain.confidence import EvidenceQuality, confidence_from_evidence
from app.domain.enums import Classification, LineCategory, ReadingType
from app.domain.money import ZERO, format_decimal_nl
from app.domain.units import convert_quantity
from app.models import Invoice, InvoiceLine, MeterReading

METER_RULE = "meter_consumption"
CONTINUITY_RULE = "meter_continuity"
ESTIMATED_RULE = "estimated_reading"
TREND_RULE = "consumption_trend"

REGISTER_CATEGORY = {
    "NORMAL": LineCategory.ELECTRICITY_NORMAL,
    "LOW": LineCategory.ELECTRICITY_LOW,
    "SINGLE": LineCategory.ELECTRICITY_SINGLE,
    "GAS": LineCategory.GAS_SUPPLY,
}
REGISTER_NL = {"NORMAL": "normaal", "LOW": "dal", "SINGLE": "enkel", "GAS": "gas", "FEED_IN": "teruglevering"}
DATE_SLACK = timedelta(days=1)


def _lines_for(inv: Invoice, category: LineCategory) -> list[InvoiceLine]:
    return [li for li in inv.lines if li.category == category and li.quantity is not None]


def _invoiced_quantity(lines: list[InvoiceLine], unit: str) -> Decimal | None:
    total = ZERO
    for li in lines:
        q = convert_quantity(li.quantity, li.unit, unit)
        if q is None:
            return None
        total += q
    return total


def _unit_price(lines: list[InvoiceLine], unit: str) -> Decimal | None:
    qty = _invoiced_quantity(lines, unit)
    amount = sum((li.amount for li in lines if li.amount is not None), ZERO)
    if not qty:
        return None
    return amount / qty


def _compare(ctx, inv, register, begin: MeterReading, end: MeterReading, source_label: str, key: str):
    category = REGISTER_CATEGORY.get(register)
    if category is None:
        return None
    lines = _lines_for(inv, category)
    if not lines:
        return None
    unit = end.unit
    invoiced = _invoiced_quantity(lines, unit)
    if invoiced is None:
        return None
    multiplier = end.multiplier or Decimal(1)
    raw = end.reading - begin.reading
    if raw < 0:
        return Finding(
            rule_id=METER_RULE, rule_version="1.0", category="consumption", classification=Classification.ANOMALY,
            confidence=confidence_from_evidence([EvidenceQuality.DERIVED]),
            title=f"Eindstand lager dan beginstand ({inv.label}, {REGISTER_NL.get(register, register)})",
            description=(f"Voor {invoice_ref(inv)} is de eindstand ({format_decimal_nl(end.reading)}) lager dan de "
                         f"beginstand ({format_decimal_nl(begin.reading)}) volgens {source_label}. Mogelijk een "
                         "meterwissel, telwerk-overloop of foutieve opname."),
            reason="Negatief verbruik uit meterstanden.", invoice=inv, unit=unit,
            actual=raw, evidence=[reading_evidence(begin), reading_evidence(end)], key=f"neg:{key}",
        )
    expected = raw * multiplier
    excess = (invoiced - expected) * charge_sign(inv)
    if abs(invoiced - expected) <= ctx.settings.quantity_tolerance:
        return None
    price = _unit_price(lines, unit)
    recovery = excess * price if (price is not None and excess > 0) else ZERO
    estimated = ReadingType.ESTIMATED in (begin.reading_type, end.reading_type)
    qualities = [invoice_quality(inv, lines[0])]
    if estimated:
        qualities.append(EvidenceQuality.ESTIMATED)
    if begin.source == "METER_DATA" and (begin.reading_date != inv.billing_period_start
                                         or end.reading_date != inv.billing_period_end):
        qualities.append(EvidenceQuality.DERIVED)
    mult_txt = f" × vermenigvuldigingsfactor {format_decimal_nl(multiplier)}" if multiplier != 1 else ""
    return Finding(
        rule_id=METER_RULE, rule_version="1.0", category="consumption",
        classification=Classification.POTENTIAL_ERROR,
        confidence=confidence_from_evidence(qualities, extraction_confidence=lines[0].confidence),
        title=f"Gefactureerd verbruik wijkt af van meterstanden ({inv.label}, {REGISTER_NL.get(register, register)})",
        description=(
            f"{upper_first(invoice_ref(inv))} rekent {format_decimal_nl(invoiced)} {unit} "
            f"({REGISTER_NL.get(register, register)}). Volgens {source_label} is het verbruik "
            f"({format_decimal_nl(end.reading)} − {format_decimal_nl(begin.reading)}){mult_txt} = "
            f"{format_decimal_nl(expected)} {unit}. Verschil: {format_decimal_nl(invoiced - expected)} {unit}."
            + (f" Mogelijke discrepantie: {eur(recovery)} (excl. energiebelasting en btw)." if recovery else "")
            + (" Let op: één of beide meterstanden zijn geschat." if estimated else "")
        ),
        reason=f"Gefactureerd volume ≠ verschil in meterstanden ({source_label}).",
        invoice=inv, line=lines[0], unit=unit, actual=invoiced, expected=expected, difference=invoiced - expected,
        potential_recovery=recovery,
        calculation=[
            f"Beginstand {begin.reading_date:%d-%m-%Y}: {format_decimal_nl(begin.reading)}",
            f"Eindstand {end.reading_date:%d-%m-%Y}: {format_decimal_nl(end.reading)}",
            f"Verbruik: ({format_decimal_nl(end.reading)} − {format_decimal_nl(begin.reading)}){mult_txt} = "
            f"{format_decimal_nl(expected)} {unit}",
            f"Gefactureerd: {format_decimal_nl(invoiced)} {unit}",
            f"Verschil: {format_decimal_nl(invoiced - expected)} {unit}",
        ] + ([f"Waarde: {format_decimal_nl(excess)} {unit} × gemiddeld tarief € "
              f"{format_decimal_nl(price.quantize(Decimal('0.00001')))} = {eur(recovery)}"] if recovery else []),
        evidence=[reading_evidence(begin), reading_evidence(end)] + [line_evidence(li) for li in lines],
        key=key,
    )


def _nearest(readings: list[MeterReading], day) -> MeterReading | None:
    candidates = [r for r in readings if abs(r.reading_date - day) <= DATE_SLACK]
    if not candidates:
        return None
    return min(candidates, key=lambda r: (abs(r.reading_date - day), r.reading_type != ReadingType.ACTUAL))


def detect_meter_consumption(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    by_invoice: dict[str, list[MeterReading]] = defaultdict(list)
    external: dict[tuple[str, str], list[MeterReading]] = defaultdict(list)
    for r in ctx.meter_readings:
        if r.invoice_id:
            by_invoice[r.invoice_id].append(r)
        elif r.ean:
            external[(r.ean, r.register)].append(r)
    for inv in ctx.active_invoices:
        # A) readings printed on the invoice itself
        regs: dict[str, list[MeterReading]] = defaultdict(list)
        for r in by_invoice.get(inv.id, []):
            regs[r.register].append(r)
        for register, rs in regs.items():
            if len(rs) < 2:
                continue
            rs.sort(key=lambda r: r.reading_date)
            f = _compare(ctx, inv, register, rs[0], rs[-1], "de meterstanden op de factuur", f"inv:{register}")
            if f:
                findings.append(f)
        # B) independent meter data for the same connection
        if not inv.ean or inv.period is None:
            continue
        for (ean, register), rs in external.items():
            if ean != inv.ean:
                continue
            begin = _nearest(rs, inv.billing_period_start)
            end = _nearest(rs, inv.billing_period_end)
            if begin and end and begin is not end:
                f = _compare(ctx, inv, register, begin, end, "de aangeleverde meterdata", f"ext:{register}")
                if f:
                    findings.append(f)
    return findings


def detect_reading_continuity(ctx: AnalysisContext) -> list[Finding]:
    """End reading of invoice N−1 should equal the start reading of invoice N."""
    series: dict[tuple[str, str], list[tuple[Invoice, MeterReading, MeterReading]]] = defaultdict(list)
    invoices = {i.id: i for i in ctx.regular_invoices}
    per_invoice: dict[tuple[str, str], list[MeterReading]] = defaultdict(list)
    for r in ctx.meter_readings:
        if r.invoice_id in invoices:
            per_invoice[(r.invoice_id, r.register)].append(r)
    for (inv_id, register), rs in per_invoice.items():
        inv = invoices[inv_id]
        if len(rs) >= 2 and inv.ean:
            rs.sort(key=lambda r: r.reading_date)
            series[(inv.ean, register)].append((inv, rs[0], rs[-1]))
    findings: list[Finding] = []
    for (_ean, register), items in series.items():
        items.sort(key=lambda t: t[1].reading_date)
        for (prev_inv, _, prev_end), (inv, begin, _) in zip(items, items[1:], strict=False):
            if begin.reading >= prev_end.reading:
                continue
            multiplier = begin.multiplier or Decimal(1)
            double_qty = (prev_end.reading - begin.reading) * multiplier
            lines = _lines_for(inv, REGISTER_CATEGORY.get(register, LineCategory.OTHER))
            price = _unit_price(lines, begin.unit) if lines else None
            recovery = double_qty * price if price else ZERO
            findings.append(Finding(
                rule_id=CONTINUITY_RULE, rule_version="1.0", category="consumption",
                classification=Classification.POTENTIAL_ERROR,
                confidence=confidence_from_evidence([invoice_quality(inv)] + (
                    [EvidenceQuality.ESTIMATED] if ReadingType.ESTIMATED in (begin.reading_type,
                                                                            prev_end.reading_type) else [])),
                title=f"Beginstand lager dan vorige eindstand ({inv.label}, {REGISTER_NL.get(register, register)})",
                description=(
                    f"De beginstand op {invoice_ref(inv)} ({format_decimal_nl(begin.reading)}) is lager dan de "
                    f"eindstand op {invoice_ref(prev_inv)} ({format_decimal_nl(prev_end.reading)}). Het verbruik "
                    f"tussen deze standen ({format_decimal_nl(double_qty)} {begin.unit}) is mogelijk twee keer "
                    f"gefactureerd. Mogelijke discrepantie: {eur(recovery)} (excl. energiebelasting en btw)."
                ),
                reason="Opeenvolgende facturen sluiten niet aan op meterstanden (overlap in verbruik).",
                invoice=inv, unit=begin.unit, actual=begin.reading, expected=prev_end.reading,
                difference=begin.reading - prev_end.reading, potential_recovery=recovery,
                calculation=[f"Vorige eindstand: {format_decimal_nl(prev_end.reading)}",
                             f"Nieuwe beginstand: {format_decimal_nl(begin.reading)}",
                             f"Dubbel gefactureerd volume: {format_decimal_nl(double_qty)} {begin.unit}"]
                + ([f"× gemiddeld tarief € {format_decimal_nl(price.quantize(Decimal('0.00001')))} = {eur(recovery)}"]
                   if price else []),
                evidence=[reading_evidence(prev_end), reading_evidence(begin)],
                key=f"{register}:{prev_inv.id}",
            ))
    return findings


def detect_estimated_readings(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    ids = {i.id: i for i in ctx.active_invoices}
    estimated: dict[str, list[MeterReading]] = defaultdict(list)
    for r in ctx.meter_readings:
        if r.invoice_id in ids and r.reading_type == ReadingType.ESTIMATED:
            estimated[r.invoice_id].append(r)
    for inv_id, rs in estimated.items():
        inv = ids[inv_id]
        findings.append(Finding(
            rule_id=ESTIMATED_RULE, rule_version="1.0", category="consumption",
            classification=Classification.ANOMALY,
            confidence=confidence_from_evidence([EvidenceQuality.ESTIMATED]),
            title=f"Factuur gebaseerd op geschatte meterstand ({inv.label})",
            description=(f"{upper_first(invoice_ref(inv))} gebruikt {len(rs)} geschatte meterstand(en). Vergelijk met "
                         "werkelijke standen (bijv. meterdata van de netbeheerder of een eigen opname) om te bepalen "
                         "of het verbruik is overschat. Op zichzelf is dit geen fout."),
            reason="Geschatte in plaats van werkelijke meterstand.",
            invoice=inv, evidence=[reading_evidence(r) for r in rs], key="estimated",
        ))
    return findings


def _group(category: LineCategory) -> str | None:
    if category == LineCategory.GAS_SUPPLY:
        return "gas"
    if category in (LineCategory.ELECTRICITY_NORMAL, LineCategory.ELECTRICITY_LOW, LineCategory.ELECTRICITY_SINGLE):
        return "electricity"
    return None


def detect_consumption_trend(ctx: AnalysisContext) -> list[Finding]:
    s = ctx.settings
    series: dict[tuple[str, str], list[tuple[Invoice, Decimal]]] = defaultdict(list)
    for inv in ctx.regular_invoices:
        if inv.period is None or not inv.ean:
            continue
        totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for li in inv.lines:
            g = _group(li.category)
            if g and li.quantity is not None:
                q = convert_quantity(li.quantity, li.unit, "m3" if g == "gas" else "kWh")
                if q is not None:
                    totals[g] += q
        for g, qty in totals.items():
            series[(inv.ean, g)].append((inv, qty / Decimal(inv.period.days)))
    findings: list[Finding] = []
    for (_ean, g), points in series.items():
        for inv, daily in points:
            if g == "gas":  # strongly seasonal: only compare with the same calendar month in other years
                month = inv.billing_period_start.month
                others = [d for i, d in points if i is not inv and i.billing_period_start.month == month]
                basis = "dezelfde maand in andere jaren"
                if len(others) < 1:
                    continue
            else:
                others = [d for i, d in points if i is not inv]
                basis = f"de mediaan van {len(others)} andere perioden"
                if len(others) < s.min_history_periods:
                    continue
            median = Decimal(statistics.median(others))
            if median <= 0:
                continue
            ratio = daily / median
            if s.consumption_drop_ratio < ratio < s.consumption_spike_ratio:
                continue
            unit = "m3" if g == "gas" else "kWh"
            spike = ratio >= s.consumption_spike_ratio
            findings.append(Finding(
                rule_id=TREND_RULE, rule_version="1.0", category="consumption",
                classification=Classification.ANOMALY,
                confidence=confidence_from_evidence([EvidenceQuality.STATISTICAL]),
                title=(f"Ongebruikelijk {'hoog' if spike else 'laag'} verbruik ({inv.label})"),
                description=(
                    f"Het gemiddelde dagverbruik op {invoice_ref(inv)} is "
                    f"{format_decimal_nl(daily.quantize(Decimal('0.1')))} {unit}/dag, "
                    f"{format_decimal_nl(ratio.quantize(Decimal('0.01')))}× {basis} "
                    f"({format_decimal_nl(median.quantize(Decimal('0.1')))} {unit}/dag). "
                    + ("Dit kan wijzen op een meet- of factuurfout, maar ook op een werkelijke verandering in "
                       "gebruik. Controleer met meterstanden." if spike else
                       "Dit kan wijzen op ontbrekende facturering (latere naheffing) of een meetprobleem.")
                    + " Dit is een statistische afwijking, geen bewijs."
                ),
                reason="Dagverbruik wijkt sterk af van de eigen historie.",
                invoice=inv, unit=f"{unit}/dag", actual=daily.quantize(Decimal("0.0001")),
                expected=median.quantize(Decimal("0.0001")), difference=(daily - median).quantize(Decimal("0.0001")),
                calculation=[f"Dagverbruik = totaal / {inv.period.days} dagen = "
                             f"{format_decimal_nl(daily.quantize(Decimal('0.01')))} {unit}/dag",
                             f"Referentie ({basis}) = {format_decimal_nl(median.quantize(Decimal('0.01')))} {unit}/dag",
                             f"Verhouding = {format_decimal_nl(ratio.quantize(Decimal('0.01')))}"],
                key=g,
            ))
    return findings


DETECTORS = [
    Detector(METER_RULE, "1.0", "Gefactureerd verbruik vs meterstanden", detect_meter_consumption),
    Detector(CONTINUITY_RULE, "1.0", "Aansluiting begin- en eindstanden tussen facturen", detect_reading_continuity),
    Detector(ESTIMATED_RULE, "1.0", "Geschatte meterstanden", detect_estimated_readings),
    Detector(TREND_RULE, "1.0", "Verbruikspieken en -dalen t.o.v. eigen historie", detect_consumption_trend),
]
