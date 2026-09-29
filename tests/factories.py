"""Reusable synthetic invoice builders for tests."""

from datetime import date
from decimal import Decimal

from app.devtools.synthetic import SynInvoice, SynLine, make_ean

D = Decimal
EAN_1 = make_ean(1)
EAN_2 = make_ean(2)


def electricity_invoice(number="2026-0001", start=date(2026, 1, 1), end=date(2026, 1, 31), *,
                        normal_qty=D("1000"), normal_price=D("0.20000"), normal_amount=None,
                        low_qty=D("500"), low_price=D("0.15000"), fixed=D("7.50"), ean=EAN_1,
                        extra_lines=None, **kw) -> SynInvoice:
    lines = [
        SynLine("Levering elektriciteit normaaltarief", normal_qty, "kWh", normal_price, normal_amount),
        SynLine("Levering elektriciteit daltarief", low_qty, "kWh", low_price),
        SynLine("Vaste leveringskosten", D("1"), "maand", fixed),
    ]
    lines += extra_lines or []
    return SynInvoice(invoice_number=number, invoice_date=end, period_start=start, period_end=end,
                      ean=ean, lines=lines, **kw)
