from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.db_types import Money, Quantity, UnitPrice
from app.domain.enums import Commodity, ExtractionMethod, InvoiceType, LineCategory, RateKind, ReadingType
from app.domain.periods import Period
from app.models.base import IdMixin, TimestampMixin, enum_type


class Invoice(IdMixin, TimestampMixin, Base):
    __tablename__ = "invoices"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    source_row: Mapped[int | None] = mapped_column(Integer)  # first row in a tabular source
    supplier: Mapped[str | None] = mapped_column(String(200))
    invoice_number: Mapped[str | None] = mapped_column(String(100), index=True)
    invoice_type: Mapped[InvoiceType] = mapped_column(enum_type(InvoiceType), default=InvoiceType.INVOICE)
    corrects_invoice_number: Mapped[str | None] = mapped_column(String(100))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    billing_period_start: Mapped[date | None] = mapped_column(Date)
    billing_period_end: Mapped[date | None] = mapped_column(Date)
    ean: Mapped[str | None] = mapped_column(String(18), index=True)
    meter_number: Mapped[str | None] = mapped_column(String(50))
    commodity: Mapped[Commodity] = mapped_column(enum_type(Commodity), default=Commodity.UNKNOWN)
    customer_name: Mapped[str | None] = mapped_column(String(200))
    subtotal_excl_vat: Mapped[Decimal | None] = mapped_column(Money)
    vat_rate: Mapped[Decimal | None] = mapped_column(Money)
    vat_amount: Mapped[Decimal | None] = mapped_column(Money)
    total_incl_vat: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    verified_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.position"
    )

    @property
    def period(self) -> Period | None:
        if self.billing_period_start and self.billing_period_end and (
            self.billing_period_start <= self.billing_period_end
        ):
            return Period(self.billing_period_start, self.billing_period_end)
        return None

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None

    @property
    def label(self) -> str:
        return self.invoice_number or f"(zonder nummer, {self.id[:8]})"


class InvoiceLine(IdMixin, Base):
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[LineCategory] = mapped_column(enum_type(LineCategory), default=LineCategory.OTHER)
    category_confidence: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    unit: Mapped[str | None] = mapped_column(String(20))
    unit_price: Mapped[Decimal | None] = mapped_column(UnitPrice)
    amount: Mapped[Decimal | None] = mapped_column(Money)
    vat_rate: Mapped[Decimal | None] = mapped_column(Money)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_row: Mapped[int | None] = mapped_column(Integer)
    source_text: Mapped[str | None] = mapped_column(Text)
    source_bbox: Mapped[list | None] = mapped_column(JSON)
    extraction_method: Mapped[ExtractionMethod] = mapped_column(enum_type(ExtractionMethod))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")

    @property
    def period(self) -> Period | None:
        if self.period_start and self.period_end and self.period_start <= self.period_end:
            return Period(self.period_start, self.period_end)
        return self.invoice.period if self.invoice else None


class MeterReading(IdMixin, Base):
    __tablename__ = "meter_readings"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"))
    ean: Mapped[str | None] = mapped_column(String(18), index=True)
    meter_number: Mapped[str | None] = mapped_column(String(50))
    register: Mapped[str] = mapped_column(String(20), default="SINGLE")  # NORMAL, LOW, SINGLE, GAS, FEED_IN
    reading_date: Mapped[date] = mapped_column(Date)
    reading: Mapped[Decimal] = mapped_column(Quantity)
    reading_type: Mapped[ReadingType] = mapped_column(enum_type(ReadingType), default=ReadingType.UNKNOWN)
    multiplier: Mapped[Decimal] = mapped_column(Quantity, default=Decimal(1))
    unit: Mapped[str] = mapped_column(String(10), default="kWh")
    source: Mapped[str | None] = mapped_column(String(50))  # INVOICE, METER_DATA, MANUAL, P4
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_row: Mapped[int | None] = mapped_column(Integer)
    source_text: Mapped[str | None] = mapped_column(Text)


class Contract(IdMixin, TimestampMixin, Base):
    __tablename__ = "contracts"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    supplier: Mapped[str] = mapped_column(String(200))
    contract_reference: Mapped[str | None] = mapped_column(String(100))
    commodity: Mapped[Commodity] = mapped_column(enum_type(Commodity), default=Commodity.ELECTRICITY)
    ean: Mapped[str | None] = mapped_column(String(18))  # empty = all connections of client at supplier
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    indexation: Mapped[str | None] = mapped_column(Text)  # description; actual prices as dated rows
    relevant_conditions: Mapped[str | None] = mapped_column(Text)
    verified_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    prices: Mapped[list["ContractPrice"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan", order_by="ContractPrice.valid_from"
    )

    @property
    def period(self) -> Period:
        return Period(self.start_date, self.end_date or date(9999, 12, 31))


class ContractPrice(IdMixin, Base):
    """One agreed tariff: e.g. ELECTRICITY_NORMAL 0.18450 EUR/kWh valid 2025-01-01..2025-12-31."""

    __tablename__ = "contract_prices"

    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    category: Mapped[LineCategory] = mapped_column(enum_type(LineCategory))
    unit: Mapped[str] = mapped_column(String(20))  # kWh, m3, month, day, year
    price: Mapped[Decimal] = mapped_column(UnitPrice)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_text: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    contract: Mapped[Contract] = relationship(back_populates="prices")

    @property
    def period(self) -> Period:
        return Period(self.valid_from, self.valid_to or date(9999, 12, 31))


class ReferenceRate(IdMixin, TimestampMixin, Base):
    """Legally defined, time-dependent rates (VAT, energy tax brackets).

    Nothing is shipped: an admin enters each rate from the official source. Detectors only
    use rows with verified_at set, and always record the row id used.
    """

    __tablename__ = "reference_rates"

    kind: Mapped[RateKind] = mapped_column(enum_type(RateKind))
    bracket_from: Mapped[Decimal | None] = mapped_column(Quantity)  # annual consumption lower bound
    bracket_to: Mapped[Decimal | None] = mapped_column(Quantity)  # upper bound (None = unbounded)
    rate: Mapped[Decimal] = mapped_column(UnitPrice)  # EUR per unit, or percentage for VAT
    unit: Mapped[str] = mapped_column(String(20))  # kWh, m3, percent
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source_reference: Mapped[str] = mapped_column(Text)  # URL / legal citation
    notes: Mapped[str | None] = mapped_column(Text)
    verified_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict | None] = mapped_column(JSON)
