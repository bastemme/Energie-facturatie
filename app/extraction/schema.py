"""Intermediate extraction results. Every value carries its provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.domain.enums import Commodity, ExtractionMethod, InvoiceType, LineCategory, ReadingType


@dataclass
class Field:
    value: Any
    raw_text: str | None = None
    page: int | None = None
    bbox: list[float] | None = None
    row: int | None = None
    confidence: float = 1.0
    method: ExtractionMethod = ExtractionMethod.REGEX


@dataclass
class ExtractedLine:
    description: str
    category: LineCategory
    category_confidence: float
    quantity: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    amount: Decimal | None
    vat_rate: Decimal | None = None
    period_start: date | None = None
    period_end: date | None = None
    page: int | None = None
    row: int | None = None
    bbox: list[float] | None = None
    source_text: str | None = None
    method: ExtractionMethod = ExtractionMethod.REGEX
    confidence: float = 1.0


@dataclass
class ExtractedReading:
    reading_date: date
    reading: Decimal
    reading_type: ReadingType = ReadingType.UNKNOWN
    register: str = "SINGLE"
    ean: str | None = None
    meter_number: str | None = None
    multiplier: Decimal = Decimal(1)
    unit: str = "kWh"
    page: int | None = None
    row: int | None = None
    source_text: str | None = None


@dataclass
class ExtractedInvoice:
    fields: dict[str, Field] = field(default_factory=dict)
    lines: list[ExtractedLine] = field(default_factory=list)
    readings: list[ExtractedReading] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source_row: int | None = None

    def get(self, name: str) -> Any:
        f = self.fields.get(name)
        return f.value if f else None

    @property
    def invoice_type(self) -> InvoiceType:
        return self.get("invoice_type") or InvoiceType.INVOICE

    @property
    def commodity(self) -> Commodity:
        return self.get("commodity") or Commodity.UNKNOWN


@dataclass
class ExtractionResult:
    invoices: list[ExtractedInvoice] = field(default_factory=list)
    readings: list[ExtractedReading] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    page_count: int | None = None
    needs_ocr: bool = False
    confidence: float = 0.0
