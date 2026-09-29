from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.db_types import Money
from app.domain.enums import Classification, Confidence, ReviewStatus, Severity
from app.models.base import IdMixin, TimestampMixin, enum_type


class AnalysisRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "analysis_runs"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    started_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    rule_versions: Mapped[dict] = mapped_column(JSON)
    invoices_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    findings_total: Mapped[int] = mapped_column(Integer, default=0)
    findings_new: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list | None] = mapped_column(JSON)


class Anomaly(IdMixin, TimestampMixin, Base):
    __tablename__ = "anomalies"
    __table_args__ = (UniqueConstraint("client_id", "fingerprint", name="uq_anomaly_fingerprint"),)

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    invoice_line_id: Mapped[str | None] = mapped_column(ForeignKey("invoice_lines.id", ondelete="SET NULL"))
    analysis_run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"))
    case_id: Mapped[str | None] = mapped_column(ForeignKey("recovery_cases.id", ondelete="SET NULL"), index=True)

    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    rule_version: Mapped[str] = mapped_column(String(16))
    fingerprint: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(64))
    classification: Mapped[Classification] = mapped_column(enum_type(Classification))
    severity: Mapped[Severity] = mapped_column(enum_type(Severity))
    confidence: Mapped[Confidence] = mapped_column(enum_type(Confidence))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(10), default="EUR")
    actual_value: Mapped[Decimal | None] = mapped_column(Money)
    expected_value: Mapped[Decimal | None] = mapped_column(Money)
    difference: Mapped[Decimal | None] = mapped_column(Money)
    potential_recovery: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    calculation: Mapped[list] = mapped_column(JSON)  # list[str] human-readable steps
    evidence: Mapped[list] = mapped_column(JSON)  # list[dict] source references
    requires_verification: Mapped[bool] = mapped_column(Boolean, default=True)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)  # not reproduced by latest run

    review_status: Mapped[ReviewStatus] = mapped_column(enum_type(ReviewStatus), default=ReviewStatus.OPEN)
    reviewed_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[str | None] = mapped_column(Text)
    duplicate_of_id: Mapped[str | None] = mapped_column(ForeignKey("anomalies.id", ondelete="SET NULL"))

    invoice = relationship("Invoice")
    invoice_line = relationship("InvoiceLine")
    case = relationship("RecoveryCase", back_populates="anomalies")

    @property
    def is_confirmed(self) -> bool:
        return self.review_status == ReviewStatus.CONFIRMED
