"""Generic parser for Dutch energy invoices with a text layer.

Supplier-specific parsers can be registered in `SUPPLIER_PARSERS`; they take precedence once
we have real sample invoices for that supplier. The generic parser uses Dutch label patterns
and never fills a value it cannot point to on the page.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal

from app.domain.ean import is_valid_ean18, normalize_ean
from app.domain.enums import Commodity, ExtractionMethod, InvoiceType, LineCategory, ReadingType
from app.domain.money import round_cents
from app.domain.units import normalize_unit
from app.extraction.categories import classify_line
from app.extraction.parsing import AMOUNT_PATTERN, DATE_PATTERN, parse_date, parse_decimal
from app.extraction.pdf_text import PdfText, TextLine
from app.extraction.schema import ExtractedInvoice, ExtractedLine, ExtractedReading, ExtractionResult, Field

# Supplier names are matched only when literally present in the document text.
KNOWN_SUPPLIERS = [
    "Vattenfall", "Eneco", "Essent", "Greenchoice", "Budget Energie", "ENGIE", "Vandebron", "Pure Energie",
    "Energiedirect", "Oxxio", "DELTA Energie", "Innova Energie", "Frank Energie", "Zonneplan",
    "Coolblue Energie", "NextEnergy", "Hollandse Energie Maatschappij", "Gazprom Energy", "Scholt Energy",
    "SEPA Green", "Axpo", "Uniper", "TotalEnergies", "Shell Energy", "Anode Energie", "Energie VanOns",
    "Liander", "Enexis", "Stedin", "Westland Infra", "Coteq", "Rendo",
]

_D = DATE_PATTERN
_A = AMOUNT_PATTERN
_UNIT = r"kWh|MWh|m3|m³|Nm3|kW|dagen|dag|maanden|maand|mnd\.?|jaar|stuks?|st"

RE_INVOICE_NUMBER = re.compile(
    r"(?:factuurnummer|factuurnr\.?|factuur\s+nr\.?|notanummer|nota\s*nr\.?|creditnota(?:nummer)?\s*nr\.?|"
    r"creditnotanummer|invoice\s*(?:number|no\.?))\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/.]{2,40})",
    re.I,
)
RE_INVOICE_DATE = re.compile(
    rf"(?:factuurdatum|datum\s+factuur|notadatum|invoice\s+date|datum)\s*:?\s*({_D})", re.I
)
RE_PERIOD = re.compile(
    rf"(?:periode|factuurperiode|leveringsperiode|verbruiksperiode|afrekenperiode|afrekeningsperiode)\s*:?\s*"
    rf"({_D})\s*(?:t/m|tm|tot\s+en\s+met|-|–|tot)\s*({_D})",
    re.I,
)
RE_EAN_LABELED = re.compile(
    r"(?:ean(?:[-\s]?code)?|aansluit(?:ings)?nummer|aansluiting)\s*:?\s*(\d[\d ]{16,24}\d)", re.I
)
RE_EAN_BARE = re.compile(r"\b(87\d{16})\b")
RE_METER = re.compile(r"meter\s*(?:nummer|nr\.?)\s*:?\s*([A-Z0-9][A-Z0-9\-]{3,29})", re.I)
RE_SUPPLIER_LABEL = re.compile(r"(?:leverancier|energieleverancier)\s*:?\s*([A-Za-z][\w .&-]{2,60})", re.I)
RE_CUSTOMER = re.compile(r"(?:klantnaam|debiteur|t\.a\.v\.|ten name van)\s*:?\s*(.{3,80})", re.I)
RE_SUBTOTAL = re.compile(
    rf"(?:subtotaal|totaal\s+excl(?:\.|usief)?\s*btw|bedrag\s+excl(?:\.|usief)?\s*btw)\s*:?\s*({_A})\s*$", re.I
)
RE_VAT = re.compile(
    rf"^(?:btw|omzetbelasting)\s*(?:\(?\s*(\d{{1,2}}(?:[.,]\d+)?)\s*%\s*\)?)?"
    rf"(?:\s*over\s*({_A}))?\s*:?\s*({_A})\s*$",
    re.I,
)
RE_TOTAL = re.compile(
    rf"(?:totaal\s+(?:incl(?:\.|usief)?\s*btw|te\s+betalen|factuurbedrag|te\s+ontvangen)|te\s+betalen(?:\s+bedrag)?|"
    rf"totaalbedrag|factuurbedrag|te\s+ontvangen)\s*:?\s*({_A})\s*$",
    re.I,
)
RE_CREDIT = re.compile(r"\bcredit\s*(?:nota|factuur)\b|\bcreditnota\b|\bcreditfactuur\b", re.I)
RE_CORRECTS = re.compile(
    r"(?:correctie\s+(?:op|van)\s+factuur|credit(?:ering)?\s+(?:op|van)\s+factuur|betreft\s+factuur|"
    r"vervangt\s+factuur|originele\s+factuur)\s*(?:nummer|nr\.?)?\s*:?\s*([A-Z0-9][A-Z0-9\-/.]{2,40})",
    re.I,
)
RE_MULTIPLIER = re.compile(r"(?:vermenigvuldigings?factor|meterfactor|multiplier)\s*:?\s*(\d+(?:[.,]\d+)?)", re.I)
RE_READING = re.compile(
    rf"(begin|eind)stand\s*(normaal|dal|enkel|piek|laag|hoog|gas)?\s*:?\s*(?:({_D})\s+)?"
    rf"(-?\d[\d.]*(?:,\d+)?)\s*(?:kWh|m3|m³)?\s*\(?\s*(werkelijk|geschat|opgenomen|berekend|gemeten)?\s*\)?",
    re.I,
)
RE_LINE = re.compile(
    rf"^(?P<desc>.*?[A-Za-zÀ-ÿ].*?)\s+(?P<qty>-?\d[\d.]*(?:,\d+)?)\s*(?P<unit>{_UNIT})\.?\s+"
    rf"(?:x\s+|à\s+|a\s+)?(?P<price>-?\s?€?\s?-?\d[\d.]*,\d+)\s*(?:(?:/|per)\s*(?:{_UNIT}))?\s+"
    rf"(?P<amount>{_A})$",
    re.I,
)
RE_LINE_AMOUNT_ONLY = re.compile(rf"^(?P<desc>.*?[A-Za-zÀ-ÿ].*?)\s+(?P<amount>{_A})$", re.I)
RE_NOT_A_LINE = re.compile(
    r"^(?:sub)?totaal|te betalen|btw|omzetbelasting|factuurbedrag|te ontvangen|reeds betaald|"
    r"termijnbedrag|voorschot|datum|iban|vervaldatum|saldo",
    re.I,
)
RE_LINE_PERIOD = re.compile(rf"({_D})\s*(?:t/m|tm|-|–)\s*({_D})")
RE_VAT_IN_LINE = re.compile(r"(\d{1,2})\s*%")

SupplierParser = Callable[[PdfText], ExtractionResult | None]
SUPPLIER_PARSERS: dict[str, SupplierParser] = {}


def parse_invoice_pdf(pdf: PdfText) -> ExtractionResult:
    if not pdf.has_text_layer:
        return ExtractionResult(
            page_count=pdf.page_count,
            needs_ocr=True,
            notes=["Geen tekstlaag gevonden (gescand document). OCR of handmatige invoer vereist."],
        )
    supplier_field = _find_supplier(pdf)
    if supplier_field and supplier_field.value in SUPPLIER_PARSERS:
        specific = SUPPLIER_PARSERS[supplier_field.value](pdf)
        if specific is not None:
            return specific

    inv = ExtractedInvoice()
    if supplier_field:
        inv.fields["supplier"] = supplier_field
    _extract_header(pdf, inv)
    _extract_lines(pdf, inv)
    _extract_readings(pdf, inv)
    _derive_commodity(inv)
    validate_invoice(inv)
    return ExtractionResult(invoices=[inv], page_count=pdf.page_count, confidence=inv.confidence,
                            notes=list(inv.notes))


def _field_from(line: TextLine, value, raw: str, confidence: float = 0.95) -> Field:
    return Field(value=value, raw_text=raw, page=line.page, bbox=line.bbox, confidence=confidence,
                 method=ExtractionMethod.REGEX)


def _first(pdf: PdfText, regex: re.Pattern[str]):
    for line in pdf.lines:
        m = regex.search(line.text)
        if m:
            return line, m
    return None, None


def _find_supplier(pdf: PdfText) -> Field | None:
    line, m = _first(pdf, RE_SUPPLIER_LABEL)
    if m:
        name = m.group(1).strip()
        for known in KNOWN_SUPPLIERS:
            if known.lower() in name.lower():
                return _field_from(line, known, m.group(0), 0.95)
        return _field_from(line, name, m.group(0), 0.8)
    for line in pdf.lines[:60]:  # letterhead area first
        for known in KNOWN_SUPPLIERS:
            if re.search(rf"\b{re.escape(known)}\b", line.text, re.I):
                return _field_from(line, known, line.text, 0.85)
    return None


def _amount(raw: str) -> Decimal | None:
    parsed = parse_decimal(raw)
    return parsed.value if parsed else None


def _extract_header(pdf: PdfText, inv: ExtractedInvoice) -> None:
    line, m = _first(pdf, RE_INVOICE_NUMBER)
    if m:
        inv.fields["invoice_number"] = _field_from(line, m.group(1).rstrip("."), m.group(0))
    line, m = _first(pdf, RE_INVOICE_DATE)
    if m and parse_date(m.group(1)):
        inv.fields["invoice_date"] = _field_from(line, parse_date(m.group(1)), m.group(0))
    line, m = _first(pdf, RE_PERIOD)
    if m:
        start, end = parse_date(m.group(1)), parse_date(m.group(2))
        if start and end:
            inv.fields["billing_period_start"] = _field_from(line, start, m.group(0))
            inv.fields["billing_period_end"] = _field_from(line, end, m.group(0))

    line, m = _first(pdf, RE_EAN_LABELED)
    ean_raw = normalize_ean(m.group(1)) if m else None
    if not (ean_raw and len(ean_raw) == 18):
        line, m = _first(pdf, RE_EAN_BARE)
        ean_raw = m.group(1) if m else None
    if ean_raw and line is not None:
        valid = is_valid_ean18(ean_raw)
        inv.fields["ean"] = _field_from(line, ean_raw, m.group(0), 0.95 if valid else 0.5)
        if not valid:
            inv.notes.append(f"EAN {ean_raw} heeft een ongeldig controlegetal — controleer de bron.")

    line, m = _first(pdf, RE_METER)
    if m:
        inv.fields["meter_number"] = _field_from(line, m.group(1), m.group(0), 0.85)
    line, m = _first(pdf, RE_CUSTOMER)
    if m:
        inv.fields["customer_name"] = _field_from(line, m.group(1).strip(), m.group(0), 0.7)

    line, m = _first(pdf, RE_SUBTOTAL)
    if m and _amount(m.group(1)) is not None:
        inv.fields["subtotal_excl_vat"] = _field_from(line, _amount(m.group(1)), m.group(0))
    line, m = _first(pdf, RE_VAT)
    if m and _amount(m.group(3)) is not None:
        inv.fields["vat_amount"] = _field_from(line, _amount(m.group(3)), m.group(0))
        if m.group(1):
            inv.fields["vat_rate"] = _field_from(line, parse_decimal(m.group(1)).value, m.group(0))
        if m.group(2) and _amount(m.group(2)) is not None:
            inv.fields["vat_base"] = _field_from(line, _amount(m.group(2)), m.group(0))
    line, m = _first(pdf, RE_TOTAL)
    if m and _amount(m.group(1)) is not None:
        inv.fields["total_incl_vat"] = _field_from(line, _amount(m.group(1)), m.group(0))

    line, m = _first(pdf, RE_CREDIT)
    if m:
        inv.fields["invoice_type"] = _field_from(line, InvoiceType.CREDIT_NOTE, m.group(0), 0.9)
    line, m = _first(pdf, RE_CORRECTS)
    if m:
        inv.fields["corrects_invoice_number"] = _field_from(line, m.group(1).rstrip("."), m.group(0), 0.85)
    line, m = _first(pdf, RE_MULTIPLIER)
    if m:
        inv.fields["multiplier"] = _field_from(line, parse_decimal(m.group(1)).value, m.group(0), 0.9)


def _extract_lines(pdf: PdfText, inv: ExtractedInvoice) -> None:
    for line in pdf.lines:
        text = line.text.strip()
        if RE_NOT_A_LINE.search(text) or _is_header_field_line(text):
            continue
        m = RE_LINE.match(text)
        if m:
            qty = parse_decimal(m.group("qty"))
            price = parse_decimal(m.group("price"))
            amount = parse_decimal(m.group("amount"))
            if not (qty and price and amount):
                continue
            desc = m.group("desc").strip(" :-")
            unit = normalize_unit(m.group("unit"))
            category, cat_conf = classify_line(desc, unit)
            conf = 0.95 - (0.1 if qty.ambiguous else 0.0)
            ex = ExtractedLine(
                description=desc, category=category, category_confidence=cat_conf,
                quantity=qty.value, unit=unit, unit_price=price.value, amount=amount.value,
                page=line.page, bbox=line.bbox, source_text=text, confidence=conf,
            )
        else:
            m = RE_LINE_AMOUNT_ONLY.match(text)
            if not m:
                continue
            desc = m.group("desc").strip(" :-")
            category, cat_conf = classify_line(desc)
            # Amount-only lines are only accepted when they are clearly an energy charge.
            if category in (LineCategory.OTHER,) or cat_conf < 0.8:
                continue
            amount = parse_decimal(m.group("amount"))
            if amount is None:
                continue
            ex = ExtractedLine(
                description=desc, category=category, category_confidence=cat_conf,
                quantity=None, unit=None, unit_price=None, amount=amount.value,
                page=line.page, bbox=line.bbox, source_text=text, confidence=0.85,
            )
        pm = RE_LINE_PERIOD.search(ex.description)
        if pm:
            ex.period_start, ex.period_end = parse_date(pm.group(1)), parse_date(pm.group(2))
        vm = RE_VAT_IN_LINE.search(ex.description)
        if vm:
            ex.vat_rate = Decimal(vm.group(1))
        inv.lines.append(ex)


def _is_header_field_line(text: str) -> bool:
    return bool(
        RE_INVOICE_NUMBER.search(text) or RE_INVOICE_DATE.search(text) or RE_PERIOD.search(text)
        or RE_READING.search(text) or RE_EAN_LABELED.search(text)
    )


def _extract_readings(pdf: PdfText, inv: ExtractedInvoice) -> None:
    ean = inv.get("ean")
    meter = inv.get("meter_number")
    multiplier = inv.get("multiplier") or Decimal(1)
    start, end = inv.get("billing_period_start"), inv.get("billing_period_end")
    for line in pdf.lines:
        for m in RE_READING.finditer(line.text):
            kind, register, date_raw, value_raw, type_raw = m.groups()
            value = parse_decimal(value_raw)
            reading_date = parse_date(date_raw) if date_raw else (start if kind.lower() == "begin" else end)
            if value is None or reading_date is None:
                continue
            reg = _register(register)
            rtype = ReadingType.UNKNOWN
            if type_raw:
                rtype = ReadingType.ESTIMATED if type_raw.lower() in ("geschat", "berekend") else ReadingType.ACTUAL
            inv.readings.append(ExtractedReading(
                reading_date=reading_date, reading=value.value, reading_type=rtype, register=reg,
                ean=ean, meter_number=meter, multiplier=multiplier, unit="m3" if reg == "GAS" else "kWh",
                page=line.page, source_text=m.group(0),
            ))


def _register(raw: str | None) -> str:
    if not raw:
        return "SINGLE"
    raw = raw.lower()
    if raw in ("dal", "laag"):
        return "LOW"
    if raw in ("normaal", "piek", "hoog"):
        return "NORMAL"
    if raw == "gas":
        return "GAS"
    return "SINGLE"


def _derive_commodity(inv: ExtractedInvoice) -> None:
    cats = {line.category for line in inv.lines}
    has_gas = bool(cats & {LineCategory.GAS_SUPPLY, LineCategory.ENERGY_TAX_GAS})
    has_elec = bool(cats & {LineCategory.ELECTRICITY_NORMAL, LineCategory.ELECTRICITY_LOW,
                            LineCategory.ELECTRICITY_SINGLE, LineCategory.ENERGY_TAX_ELECTRICITY,
                            LineCategory.ELECTRICITY_FEED_IN})
    commodity = (Commodity.MIXED if has_gas and has_elec else Commodity.GAS if has_gas
                 else Commodity.ELECTRICITY if has_elec else Commodity.UNKNOWN)
    inv.fields["commodity"] = Field(value=commodity, confidence=0.9, method=ExtractionMethod.DERIVED)


def validate_invoice(inv: ExtractedInvoice, *, tolerance: Decimal = Decimal("0.05")) -> None:
    """Deterministic plausibility checks. They lower confidence and add notes; they never fix values."""
    score = 1.0
    required = {
        "invoice_number": (0.3, "Factuurnummer niet gevonden."),
        "invoice_date": (0.1, "Factuurdatum niet gevonden."),
        "billing_period_start": (0.2, "Factuurperiode niet gevonden."),
        "total_incl_vat": (0.2, "Totaalbedrag niet gevonden."),
        "ean": (0.05, "EAN-code niet gevonden."),
    }
    for name, (penalty, note) in required.items():
        if inv.get(name) is None:
            score -= penalty
            inv.notes.append(note)
    if not inv.lines:
        score -= 0.4
        inv.notes.append("Geen factuurregels herkend.")

    start, end = inv.get("billing_period_start"), inv.get("billing_period_end")
    if start and end and start > end:
        score -= 0.3
        inv.notes.append("Einddatum van de periode ligt vóór de begindatum.")

    line_sum = round_cents(sum((line.amount for line in inv.lines if line.amount is not None), Decimal(0)))
    subtotal, vat, total = inv.get("subtotal_excl_vat"), inv.get("vat_amount"), inv.get("total_incl_vat")
    if inv.lines and subtotal is not None and abs(line_sum - subtotal) > tolerance:
        score -= 0.25
        inv.notes.append(
            f"Som van herkende regels ({line_sum}) wijkt af van subtotaal ({subtotal}): "
            "mogelijk niet alle regels herkend, of rekenfout op de factuur."
        )
    if subtotal is not None and vat is not None and total is not None and abs(subtotal + vat - total) > tolerance:
        score -= 0.1
        inv.notes.append(f"Subtotaal + btw ({subtotal + vat}) ≠ totaal ({total}).")
    if any(line.category_confidence < 0.6 for line in inv.lines):
        score -= 0.05
        inv.notes.append("Niet alle regels konden met zekerheid gecategoriseerd worden.")
    if any(line.confidence < 0.9 for line in inv.lines):
        score -= 0.05
    inv.confidence = max(0.0, round(score, 2))
