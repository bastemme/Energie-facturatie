"""Synthetic invoice generator for tests and demos.

Everything produced here is SYNTHETIC. Generated documents carry a visible 'SYNTHETISCH' marker
and must never be presented as real client data.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.domain.ean import ean_check_digit
from app.domain.money import format_decimal_nl, round_cents


def make_ean(seed: int) -> str:
    body = f"8716{seed:013d}"[:17]
    return body + str(ean_check_digit(body))


@dataclass
class SynLine:
    description: str
    quantity: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    amount: Decimal | None = None  # None → computed correctly; set to inject an arithmetic error

    def resolved_amount(self) -> Decimal:
        if self.amount is not None:
            return self.amount
        return round_cents(self.quantity * self.unit_price)


@dataclass
class SynInvoice:
    invoice_number: str
    invoice_date: date
    period_start: date
    period_end: date
    ean: str
    lines: list[SynLine]
    supplier: str = "Voorbeeld Energie B.V."
    customer: str = "Synthetisch Testbedrijf B.V."
    meter_number: str = "E0012345678"
    vat_rate: Decimal = Decimal("21")
    # (begin/eind, register, date, value, type)
    readings: list[tuple[str, str, date, Decimal, str]] = field(default_factory=list)
    credit_note: bool = False
    corrects: str | None = None
    subtotal_override: Decimal | None = None
    vat_override: Decimal | None = None
    total_override: Decimal | None = None
    multiplier: Decimal | None = None

    @property
    def subtotal(self) -> Decimal:
        if self.subtotal_override is not None:
            return self.subtotal_override
        return sum((line.resolved_amount() for line in self.lines), Decimal(0))

    @property
    def vat(self) -> Decimal:
        return self.vat_override if self.vat_override is not None else round_cents(
            self.subtotal * self.vat_rate / Decimal(100))

    @property
    def total(self) -> Decimal:
        return self.total_override if self.total_override is not None else self.subtotal + self.vat


def _nl_money(v: Decimal) -> str:
    return ("-" if v < 0 else "") + "€ " + format_decimal_nl(abs(v), 2)


def _nl_price(v: Decimal) -> str:
    return ("-" if v < 0 else "") + "€ " + format_decimal_nl(abs(v), 5)


def render_invoice_pdf(inv: SynInvoice) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    y = height - 50

    def text(x, s, size=10, right=False):
        c.setFont("Helvetica", size)
        (c.drawRightString if right else c.drawString)(x, y, s)

    text(50, inv.supplier, 14)
    text(545, "SYNTHETISCH TESTDOCUMENT", 8, right=True)
    y -= 30
    text(50, "CREDITNOTA" if inv.credit_note else "FACTUUR", 12)
    y -= 20
    text(50, f"Leverancier: {inv.supplier}")
    y -= 14
    text(50, f"Klantnaam: {inv.customer}")
    y -= 14
    text(50, f"Factuurnummer: {inv.invoice_number}")
    y -= 14
    text(50, f"Factuurdatum: {inv.invoice_date:%d-%m-%Y}")
    y -= 14
    text(50, f"Periode: {inv.period_start:%d-%m-%Y} t/m {inv.period_end:%d-%m-%Y}")
    y -= 14
    text(50, f"EAN-code: {inv.ean}")
    y -= 14
    text(50, f"Meternummer: {inv.meter_number}")
    if inv.multiplier is not None:
        y -= 14
        text(50, f"Vermenigvuldigingsfactor: {format_decimal_nl(inv.multiplier)}")
    if inv.corrects:
        y -= 14
        text(50, f"Correctie op factuur {inv.corrects}")
    for kind, reg, d, value, rtype in inv.readings:
        y -= 14
        text(50, f"{kind.capitalize()}stand {reg} {d:%d-%m-%Y} {format_decimal_nl(value)} kWh ({rtype})")
    y -= 28
    text(50, "Omschrijving", 9)
    text(330, "Hoeveelheid", 9, right=True)
    text(430, "Tarief", 9, right=True)
    text(545, "Bedrag", 9, right=True)
    for line in inv.lines:
        y -= 16
        text(50, line.description)
        if line.quantity is not None:
            text(330, f"{format_decimal_nl(line.quantity)} {line.unit}", right=True)
            text(430, _nl_price(line.unit_price) if line.unit_price is not None else "", right=True)
        text(545, _nl_money(line.resolved_amount()), right=True)
    y -= 28
    text(400, "Subtotaal")
    text(545, _nl_money(inv.subtotal), right=True)
    y -= 16
    text(400, f"BTW {format_decimal_nl(inv.vat_rate)}%")
    text(545, _nl_money(inv.vat), right=True)
    y -= 16
    text(400, "Totaal te betalen")
    text(545, _nl_money(inv.total), right=True)
    c.showPage()
    c.save()
    return buf.getvalue()


CSV_HEADER = ["factuurnummer", "factuurdatum", "leverancier", "ean", "meternummer", "periode_start",
              "periode_eind", "factuurtype", "correctie_op", "omschrijving", "hoeveelheid", "eenheid", "tarief",
              "bedrag", "btw_percentage", "subtotaal", "btw_bedrag", "totaal"]


def invoices_to_csv(invoices: list[SynInvoice]) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(CSV_HEADER)
    for inv in invoices:
        for line in inv.lines:
            w.writerow([
                inv.invoice_number, f"{inv.invoice_date:%d-%m-%Y}", inv.supplier, inv.ean, inv.meter_number,
                f"{inv.period_start:%d-%m-%Y}", f"{inv.period_end:%d-%m-%Y}",
                "creditnota" if inv.credit_note else "factuur", inv.corrects or "", line.description,
                "" if line.quantity is None else format_decimal_nl(line.quantity),
                line.unit or "", "" if line.unit_price is None else format_decimal_nl(line.unit_price),
                format_decimal_nl(line.resolved_amount(), 2), format_decimal_nl(inv.vat_rate),
                format_decimal_nl(inv.subtotal, 2), format_decimal_nl(inv.vat, 2), format_decimal_nl(inv.total, 2),
            ])
    return out.getvalue().encode("utf-8")


def render_scanned_like_pdf() -> bytes:
    """A PDF with only an image-like drawing and no text layer (simulates a scan)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.rect(50, 500, 300, 200, fill=1)
    c.showPage()
    c.save()
    return buf.getvalue()
