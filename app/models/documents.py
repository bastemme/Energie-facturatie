from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.domain.enums import DocumentStatus, DocumentType, ExtractionMethod
from app.models.base import IdMixin, TimestampMixin, enum_type


class Document(IdMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    doc_type: Mapped[DocumentType] = mapped_column(enum_type(DocumentType), default=DocumentType.UNKNOWN)
    doc_type_confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[DocumentStatus] = mapped_column(enum_type(DocumentStatus), default=DocumentStatus.UPLOADED)
    page_count: Mapped[int | None] = mapped_column(Integer)
    page_sizes: Mapped[list | None] = mapped_column(JSON)  # [[width, height], ...] in PDF points
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    processing_error: Mapped[str | None] = mapped_column(Text)
    processing_notes: Mapped[list | None] = mapped_column(JSON)  # human-readable validation notes
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    extracted_values: Mapped[list["ExtractedValue"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class ExtractedValue(IdMixin, Base):
    """Provenance of a single extracted field: value + where it came from + how sure we are."""

    __tablename__ = "extracted_values"

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    field_name: Mapped[str] = mapped_column(String(64))
    value: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)  # 1-based
    bbox: Mapped[list | None] = mapped_column(JSON)  # [x0, top, x1, bottom] in PDF points
    row_number: Mapped[int | None] = mapped_column(Integer)  # for CSV/XLSX
    confidence: Mapped[float] = mapped_column(Float)
    method: Mapped[ExtractionMethod] = mapped_column(enum_type(ExtractionMethod))

    document: Mapped[Document] = relationship(back_populates="extracted_values")
