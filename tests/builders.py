"""Direct DB builders for detection tests (faster than rendering PDFs)."""

from datetime import UTC, date, datetime
from decimal import Decimal

from app.domain.enums import Commodity, ExtractionMethod, InvoiceType, LineCategory, RateKind, ReadingType
from app.domain.money import round_cents
from app.models import Contract, ContractPrice, Invoice, InvoiceLine, MeterReading, ReferenceRate
from tests.factories import EAN_1

D = Decimal
C = LineCategory


def line(category, qty, unit, price, amount=None, desc=None, **kw):
    return dict(category=category, quantity=None if qty is None else D(str(qty)), unit=unit,
                unit_price=None if price is None else D(str(price)),
                amount=D(str(amount)) if amount is not None else round_cents(D(str(qty)) * D(str(price))),
                description=desc or category.value.lower(), **kw)


def add_invoice(db, client, number, start, end, lines, *, supplier="Voorbeeld Energie", ean=EAN_1,
                method=ExtractionMethod.CSV, vat_rate=D("21"), vat_amount=None, subtotal=None, total=None,
                invoice_type=InvoiceType.INVOICE, corrects=None, invoice_date=None, verified=False,
                extraction_confidence=1.0, commodity=Commodity.ELECTRICITY):
    inv = Invoice(client_id=client.id, supplier=supplier, invoice_number=number, invoice_date=invoice_date or end,
                  billing_period_start=start, billing_period_end=end, ean=ean, commodity=commodity,
                  invoice_type=invoice_type, corrects_invoice_number=corrects,
                  extraction_confidence=extraction_confidence,
                  verified_at=datetime.now(UTC) if verified else None)
    for pos, spec in enumerate(lines):
        inv.lines.append(InvoiceLine(position=pos, extraction_method=method, confidence=1.0, **spec))
    sub = subtotal if subtotal is not None else sum((li.amount for li in inv.lines), D(0))
    inv.subtotal_excl_vat = sub
    inv.vat_rate = vat_rate
    inv.vat_amount = vat_amount if vat_amount is not None else round_cents(sub * vat_rate / 100)
    inv.total_incl_vat = total if total is not None else inv.subtotal_excl_vat + inv.vat_amount
    db.add(inv)
    db.flush()
    return inv


def add_contract(db, client, prices, *, supplier="Voorbeeld Energie", start=date(2025, 1, 1), end=None,
                 verified=True, commodity=Commodity.ELECTRICITY, ean=None):
    c = Contract(client_id=client.id, supplier=supplier, start_date=start, end_date=end, commodity=commodity,
                 contract_reference="CT-TEST", ean=ean, verified_at=datetime.now(UTC) if verified else None)
    for category, unit, price, vf, vt in prices:
        c.prices.append(ContractPrice(category=category, unit=unit, price=D(str(price)), valid_from=vf,
                                      valid_to=vt, source_page=2, source_text="contracttarief"))
    db.add(c)
    db.flush()
    return c


def add_reading(db, client, d, value, *, register="NORMAL", rtype=ReadingType.ACTUAL, invoice=None,
                ean=EAN_1, multiplier=1, unit="kWh"):
    r = MeterReading(client_id=client.id, invoice_id=invoice.id if invoice else None, ean=ean, register=register,
                     reading_date=d, reading=D(str(value)), reading_type=rtype, multiplier=D(str(multiplier)),
                     unit=unit, source="INVOICE" if invoice else "METER_DATA", source_row=None if invoice else 2)
    db.add(r)
    db.flush()
    return r


def add_rate(db, kind, rate, unit, valid_from, *, bracket_from=None, bracket_to=None, verified=True):
    """Test-only reference rates. Values in tests are hypothetical, not real Dutch tax rates."""
    r = ReferenceRate(kind=kind, rate=D(str(rate)), unit=unit, valid_from=valid_from,
                      bracket_from=None if bracket_from is None else D(str(bracket_from)),
                      bracket_to=None if bracket_to is None else D(str(bracket_to)),
                      source_reference="TESTWAARDE — geen echt tarief",
                      verified_at=datetime.now(UTC) if verified else None)
    db.add(r)
    db.flush()
    return r


__all__ = ["C", "D", "RateKind", "add_contract", "add_invoice", "add_rate", "add_reading", "line"]
