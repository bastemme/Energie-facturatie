"""Upload → validate → encrypt → classify → extract → normalize → persist (with provenance)."""

from __future__ import annotations

import time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.enums import DocumentStatus, DocumentType, InvoiceType, ReviewStatus
from app.extraction.classify import classify_text
from app.extraction.invoice_pdf import parse_invoice_pdf
from app.extraction.pdf_text import read_pdf
from app.extraction.schema import ExtractedInvoice, ExtractedReading, ExtractionResult
from app.extraction.tabular import parse_table
from app.ingestion.storage import get_store, sha256_hex
from app.ingestion.validation import ALLOWED_EXTENSIONS, safe_filename, validate_upload
from app.models import (
    Anomaly,
    Client,
    Document,
    ExtractedValue,
    Invoice,
    InvoiceLine,
    MeterReading,
    ProcessingEvent,
    User,
)
from app.models.base import utcnow


class DuplicateDocument(Exception):
    def __init__(self, existing: Document):
        super().__init__("Dit bestand is al eerder geüpload.")
        self.existing = existing


class ReprocessBlocked(Exception):
    pass


_MIME_TO_KIND = {k.mime: k.kind for k in ALLOWED_EXTENSIONS.values()}
_INVOICE_FIELDS = (
    "supplier", "invoice_number", "corrects_invoice_number", "invoice_date", "billing_period_start",
    "billing_period_end", "ean", "meter_number", "customer_name", "subtotal_excl_vat", "vat_rate",
    "vat_amount", "total_incl_vat",
)


def ingest_upload(db: Session, client: Client, filename: str | None, content: bytes, user: User | None,
                  declared_type: DocumentType | None = None) -> Document:
    settings = get_settings()
    kind = validate_upload(filename, content, settings.max_upload_bytes)
    digest = sha256_hex(content)
    existing = db.scalar(select(Document).where(Document.client_id == client.id, Document.sha256 == digest))
    if existing:
        raise DuplicateDocument(existing)
    storage_key = get_store().put(content)
    doc = Document(
        client_id=client.id, original_filename=safe_filename(filename), storage_key=storage_key, sha256=digest,
        mime_type=kind.mime, size_bytes=len(content), uploaded_by_id=user.id if user else None,
    )
    db.add(doc)
    db.flush()
    process_document(db, doc, content=content, declared_type=declared_type)
    return doc


def process_document(db: Session, doc: Document, *, content: bytes | None = None,
                     declared_type: DocumentType | None = None) -> Document:
    started = time.perf_counter()
    _clear_previous_extraction(db, doc)
    content = content if content is not None else get_store().get(doc.storage_key)
    kind = _MIME_TO_KIND.get(doc.mime_type, "other")
    doc.processing_error = None
    try:
        if kind == "pdf":
            _process_pdf(db, doc, content, declared_type)
        elif kind in ("csv", "xlsx"):
            _process_table(db, doc, content, kind, declared_type)
        elif kind == "image":
            doc.doc_type = declared_type or DocumentType.UNKNOWN
            doc.status = DocumentStatus.NEEDS_OCR
            doc.processing_notes = ["Afbeelding: OCR is nog niet beschikbaar — handmatige invoer vereist."]
        else:
            doc.status = DocumentStatus.STORED
        success = True
    except Exception as exc:  # extraction must never crash the upload; record and route to review
        doc.status = DocumentStatus.FAILED
        doc.processing_error = f"{type(exc).__name__}: verwerking mislukt"
        success = False
    doc.processed_at = utcnow()
    db.add(ProcessingEvent(
        client_id=doc.client_id, document_id=doc.id, step="extraction",
        duration_ms=int((time.perf_counter() - started) * 1000), success=success,
        meta={"kind": kind, "status": doc.status.value},
    ))
    db.flush()
    return doc


def _clear_previous_extraction(db: Session, doc: Document) -> None:
    invoices = db.scalars(select(Invoice).where(Invoice.document_id == doc.id)).all()
    if not invoices:
        db.query(MeterReading).filter(MeterReading.document_id == doc.id).delete()
        db.query(ExtractedValue).filter(ExtractedValue.document_id == doc.id).delete()
        return
    ids = [i.id for i in invoices]
    if any(i.is_verified for i in invoices):
        raise ReprocessBlocked("Factuurgegevens zijn al geverifieerd; opnieuw verwerken zou correcties overschrijven.")
    reviewed = db.scalar(select(Anomaly.id).where(Anomaly.invoice_id.in_(ids),
                                                  Anomaly.review_status != ReviewStatus.OPEN).limit(1))
    if reviewed:
        raise ReprocessBlocked("Er zijn al beoordeelde bevindingen voor dit document.")
    db.query(MeterReading).filter(MeterReading.document_id == doc.id).delete()
    db.query(ExtractedValue).filter(ExtractedValue.document_id == doc.id).delete()
    for inv in invoices:
        db.delete(inv)
    db.flush()


def _process_pdf(db: Session, doc: Document, content: bytes, declared_type: DocumentType | None) -> None:
    pdf = read_pdf(content)
    doc.page_count = pdf.page_count
    if not pdf.has_text_layer:
        doc.doc_type = declared_type or DocumentType.UNKNOWN
        doc.status = DocumentStatus.NEEDS_OCR
        doc.processing_notes = ["Geen tekstlaag (gescand document). OCR of handmatige invoer vereist."]
        return
    detected, conf = classify_text(pdf.full_text())
    doc.doc_type = declared_type or detected
    doc.doc_type_confidence = 1.0 if declared_type else conf
    if doc.doc_type not in (DocumentType.INVOICE, DocumentType.CREDIT_NOTE):
        doc.status = DocumentStatus.STORED
        doc.processing_notes = ["Opgeslagen als ondersteunend document (geen factuur)."]
        return
    result = parse_invoice_pdf(pdf)
    if doc.doc_type == DocumentType.CREDIT_NOTE:
        for inv in result.invoices:
            inv.fields.setdefault("invoice_type", _field(InvoiceType.CREDIT_NOTE))
    _persist(db, doc, result)


def _process_table(db: Session, doc: Document, content: bytes, kind: str,
                   declared_type: DocumentType | None) -> None:
    table_kind, result = parse_table(content, kind)
    if table_kind == "invoice":
        doc.doc_type = DocumentType.INVOICE_TABLE
    elif table_kind == "meter":
        doc.doc_type = DocumentType.METER_DATA
    else:
        doc.doc_type = declared_type or DocumentType.OTHER
        doc.status = DocumentStatus.NEEDS_REVIEW
        doc.processing_notes = result.notes
        return
    doc.doc_type_confidence = 0.95
    _persist(db, doc, result)


def _field(value):
    from app.extraction.schema import Field

    return Field(value=value, confidence=1.0)


def _persist(db: Session, doc: Document, result: ExtractionResult) -> None:
    settings = get_settings()
    notes = list(result.notes)
    confidences: list[float] = []
    for ex in result.invoices:
        invoice = _persist_invoice(db, doc, ex)
        confidences.append(ex.confidence)
        if ex.notes:
            notes.extend(f"Factuur {invoice.label}: {n}" for n in ex.notes)
    for reading in result.readings:
        _persist_reading(db, doc, reading, invoice_id=None)
    if result.needs_ocr:
        doc.status = DocumentStatus.NEEDS_OCR
    elif result.invoices:
        doc.extraction_confidence = min(confidences)
        doc.status = (DocumentStatus.PROCESSED if doc.extraction_confidence >= settings.extraction_review_threshold
                      else DocumentStatus.NEEDS_REVIEW)
    elif result.readings:
        doc.extraction_confidence = result.confidence
        doc.status = DocumentStatus.PROCESSED
    else:
        doc.status = DocumentStatus.NEEDS_REVIEW
        notes.append("Geen gegevens geëxtraheerd.")
    doc.processing_notes = notes or None


def _persist_invoice(db: Session, doc: Document, ex: ExtractedInvoice) -> Invoice:
    invoice = Invoice(
        client_id=doc.client_id, document_id=doc.id, source_row=ex.source_row,
        invoice_type=ex.invoice_type, commodity=ex.commodity, extraction_confidence=ex.confidence,
    )
    for name in _INVOICE_FIELDS:
        value = ex.get(name)
        if value is not None:
            setattr(invoice, name, value)
    db.add(invoice)
    db.flush()
    for name, f in ex.fields.items():
        db.add(ExtractedValue(
            document_id=doc.id, invoice_id=invoice.id, field_name=name,
            value=None if f.value is None else str(getattr(f.value, "value", f.value)),
            raw_text=f.raw_text, page=f.page, bbox=f.bbox, row_number=f.row,
            confidence=f.confidence, method=f.method,
        ))
    for pos, line in enumerate(ex.lines):
        invoice.lines.append(InvoiceLine(
            position=pos, category=line.category, category_confidence=line.category_confidence,
            description=line.description[:500], quantity=line.quantity, unit=line.unit,
            unit_price=line.unit_price, amount=line.amount, vat_rate=line.vat_rate or invoice.vat_rate,
            period_start=line.period_start, period_end=line.period_end, source_page=line.page,
            source_row=line.row, source_text=line.source_text, source_bbox=line.bbox,
            extraction_method=line.method, confidence=line.confidence,
        ))
    for reading in ex.readings:
        _persist_reading(db, doc, reading, invoice_id=invoice.id)
    db.flush()
    return invoice


def _persist_reading(db: Session, doc: Document, r: ExtractedReading, invoice_id: str | None) -> None:
    db.add(MeterReading(
        client_id=doc.client_id, document_id=doc.id, invoice_id=invoice_id, ean=r.ean,
        meter_number=r.meter_number, register=r.register, reading_date=r.reading_date, reading=r.reading,
        reading_type=r.reading_type, multiplier=r.multiplier or Decimal(1), unit=r.unit,
        source="INVOICE" if invoice_id else "METER_DATA", source_page=r.page, source_row=r.row,
        source_text=r.source_text,
    ))
