from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.db_types import Money, Percentage
from app.domain.enums import CaseStatus
from app.models.base import IdMixin, TimestampMixin, enum_type, utcnow


class RecoveryCase(IdMixin, TimestampMixin, Base):
    __tablename__ = "recovery_cases"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    reference: Mapped[str] = mapped_column(String(40), unique=True)
    supplier: Mapped[str] = mapped_column(String(200))
    status: Mapped[CaseStatus] = mapped_column(enum_type(CaseStatus), default=CaseStatus.DETECTED)

    # Amounts are kept strictly separate — see docs/product-architecture.md §1
    disputed_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    confirmed_amount: Mapped[Decimal | None] = mapped_column(Money)
    recovered_amount: Mapped[Decimal | None] = mapped_column(Money)
    recovered_reference: Mapped[str | None] = mapped_column(String(200))  # credit note / payment ref
    success_fee_percentage: Mapped[Decimal] = mapped_column(Percentage)  # snapshot from client agreement
    success_fee: Mapped[Decimal | None] = mapped_column(Money)

    notes: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    anomalies = relationship("Anomaly", back_populates="case")
    events: Mapped[list["CaseEvent"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="CaseEvent.created_at"
    )
    client = relationship("Client")


class CaseEvent(Base):
    __tablename__ = "case_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("recovery_cases.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(40))  # STATUS_CHANGE, AMOUNT_UPDATE, NOTE, EXPORT
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSON)

    case: Mapped[RecoveryCase] = relationship(back_populates="events")
