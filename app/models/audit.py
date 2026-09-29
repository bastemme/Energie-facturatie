from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import IdMixin, utcnow


class AuditLog(IdMixin, Base):
    """Security/audit trail. Contains ids and action names only — never financial values or content.

    Deliberately has no FK to clients/users so the record of a deletion survives the deletion.
    """

    __tablename__ = "audit_log"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    client_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str | None] = mapped_column(String(40))
    object_id: Mapped[str | None] = mapped_column(String(36))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSON)


class ProcessingEvent(IdMixin, Base):
    """Cost/timing telemetry per processing step (for margin analytics). No content."""

    __tablename__ = "processing_events"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    client_id: Mapped[str | None] = mapped_column(String(36), index=True)
    document_id: Mapped[str | None] = mapped_column(String(36))
    step: Mapped[str] = mapped_column(String(40))  # extraction, analysis, ai_call, report
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(default=True)
    cost_eur: Mapped[float] = mapped_column(Float, default=0.0)  # estimated infra/AI cost (not financial data)
    meta: Mapped[dict | None] = mapped_column(JSON)
