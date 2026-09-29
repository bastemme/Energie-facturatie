"""Aggregation of potential recovery without double counting.

Several rules can describe the same euro (e.g. a line that is both arithmetically wrong and off-contract).
Totals therefore never sum findings naively:
- per invoice line: the maximum of the findings on that line;
- per invoice: max(Σ line maxima, largest invoice-level finding);
- findings without an invoice: summed.
This is deliberately conservative: it may understate, never overstate.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal
from typing import Protocol

from app.domain.money import ZERO, round_cents


class HasRecovery(Protocol):
    invoice_id: str | None
    invoice_line_id: str | None
    potential_recovery: Decimal


def conservative_total(items: Iterable[HasRecovery]) -> Decimal:
    per_line: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    per_invoice_level: dict[str, Decimal] = defaultdict(lambda: ZERO)
    loose = ZERO
    invoices: set[str] = set()
    for a in items:
        value = a.potential_recovery or ZERO
        if value <= 0:
            continue
        if a.invoice_id is None:
            loose += value
            continue
        invoices.add(a.invoice_id)
        if a.invoice_line_id:
            key = (a.invoice_id, a.invoice_line_id)
            per_line[key] = max(per_line[key], value)
        else:
            per_invoice_level[a.invoice_id] = max(per_invoice_level[a.invoice_id], value)
    total = loose
    for inv in invoices:
        line_sum = sum((v for (i, _), v in per_line.items() if i == inv), ZERO)
        total += max(line_sum, per_invoice_level[inv])
    return round_cents(total)
