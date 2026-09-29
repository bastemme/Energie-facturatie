"""Billing periods. Dutch invoices state periods inclusively ("01-01-2025 t/m 31-01-2025")."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal


@dataclass(frozen=True, order=True)
class Period:
    start: date
    end: date  # inclusive

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"period end {self.end} before start {self.start}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def overlap(self, other: Period) -> Period | None:
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        return Period(start, end) if start <= end else None

    def overlap_days(self, other: Period) -> int:
        o = self.overlap(other)
        return o.days if o else 0

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def within(self, other: Period) -> bool:
        return other.start <= self.start and self.end <= other.end

    def gap_to(self, following: Period) -> Period | None:
        """Uncovered days between this period and a later one, if any."""
        first_uncovered = self.end + timedelta(days=1)
        if following.start > first_uncovered:
            return Period(first_uncovered, following.start - timedelta(days=1))
        return None

    def fraction_of(self, other: Period) -> Decimal:
        """Share of *other*'s days that fall inside this period (for prorating)."""
        return Decimal(self.overlap_days(other)) / Decimal(other.days)

    def __str__(self) -> str:
        return f"{self.start:%d-%m-%Y} t/m {self.end:%d-%m-%Y}"


def months_between(period: Period) -> Decimal:
    """Number of calendar months covered, prorated by days for partial months.

    Used to check fixed monthly charges: a period of 01-01 t/m 31-03 is exactly 3 months,
    01-01 t/m 15-01 is 15/31 months.
    """
    total = Decimal(0)
    cursor = period.start
    while cursor <= period.end:
        month_start = cursor.replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        month_end = next_month - timedelta(days=1)
        segment_end = min(month_end, period.end)
        days_in_month = (month_end - month_start).days + 1
        total += Decimal((segment_end - cursor).days + 1) / Decimal(days_in_month)
        cursor = segment_end + timedelta(days=1)
    return total
