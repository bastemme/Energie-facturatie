import io
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.devtools.synthetic import SynLine, invoices_to_csv, render_invoice_pdf, render_scanned_like_pdf
from app.domain.enums import (
    DocumentStatus,
    DocumentType,
    ExtractionMethod,
    InvoiceType,
    LineCategory,
    ReadingType,
)
from app.ingestion.pipeline import DuplicateDocument, ingest_upload
from app.ingestion.storage import get_store
from app.ingestion.validation import UploadRejected, safe_filename
from app.models import ExtractedValue, Invoice, MeterReading
from tests.factories import EAN_1, electricity_invoice

D = Decimal


def test_pdf_invoice_extracted_with_provenance(db, client_factory):
    client = client_factory()
    syn = electricity_invoice(readings=[("begin", "normaal", date(2026, 1, 1), D("12000"), "werkelijk"),
                                        ("eind", "normaal", date(2026, 1, 31), D("13000"), "geschat")])
    doc = ingest_upload(db, client, "factuur.pdf", render_invoice_pdf(syn), None)
    assert doc.doc_type == DocumentType.INVOICE
    assert doc.status == DocumentStatus.PROCESSED, doc.processing_notes
    inv = db.scalar(select(Invoice).where(Invoice.document_id == doc.id))
    assert inv.invoice_number == "2026-0001"
    assert inv.ean == EAN_1
    assert inv.billing_period_start == date(2026, 1, 1) and inv.billing_period_end == date(2026, 1, 31)
    assert inv.subtotal_excl_vat == syn.subtotal
    assert inv.total_incl_vat == syn.total
    assert [line.category for line in inv.lines][:3] == [
        LineCategory.ELECTRICITY_NORMAL, LineCategory.ELECTRICITY_LOW, LineCategory.FIXED_SUPPLY_FEE]
    first = inv.lines[0]
    assert first.quantity == D("1000") and first.unit_price == D("0.2") and first.amount == D("200.00")
    assert first.source_page == 1 and first.source_bbox and "normaaltarief" in first.source_text
    # provenance for header fields
    ev = db.scalar(select(ExtractedValue).where(ExtractedValue.invoice_id == inv.id,
                                                ExtractedValue.field_name == "total_incl_vat"))
    assert ev.page == 1 and ev.bbox and ev.method == ExtractionMethod.REGEX
    readings = db.scalars(select(MeterReading).where(MeterReading.invoice_id == inv.id)).all()
    assert {r.reading_type for r in readings} == {ReadingType.ACTUAL, ReadingType.ESTIMATED}


def test_file_encrypted_at_rest(db, client_factory, tmp_path):
    client = client_factory()
    content = render_invoice_pdf(electricity_invoice())
    doc = ingest_upload(db, client, "f.pdf", content, None)
    store = get_store()
    raw = store._path(doc.storage_key).read_bytes()
    assert b"%PDF" not in raw
    assert store.get(doc.storage_key) == content


def test_duplicate_upload_rejected(db, client_factory):
    client = client_factory()
    content = render_invoice_pdf(electricity_invoice())
    ingest_upload(db, client, "a.pdf", content, None)
    with pytest.raises(DuplicateDocument):
        ingest_upload(db, client, "b.pdf", content, None)
    # same file for a different client is allowed (tenant isolation)
    ingest_upload(db, client_factory("Ander B.V."), "a.pdf", content, None)


def test_scanned_pdf_needs_ocr(db, client_factory):
    doc = ingest_upload(db, client_factory(), "scan.pdf", render_scanned_like_pdf(), None)
    assert doc.status == DocumentStatus.NEEDS_OCR
    assert db.scalar(select(Invoice).where(Invoice.document_id == doc.id)) is None


def test_credit_note_detected(db, client_factory):
    syn = electricity_invoice(number="C-1", credit_note=True, corrects="2026-0001")
    doc = ingest_upload(db, client_factory(), "c.pdf", render_invoice_pdf(syn), None)
    inv = db.scalar(select(Invoice).where(Invoice.document_id == doc.id))
    assert doc.doc_type == DocumentType.CREDIT_NOTE
    assert inv.invoice_type == InvoiceType.CREDIT_NOTE
    assert inv.corrects_invoice_number == "2026-0001"


def test_arithmetic_mismatch_lowers_confidence(db, client_factory):
    syn = electricity_invoice(subtotal_override=D("999.99"))
    doc = ingest_upload(db, client_factory(), "x.pdf", render_invoice_pdf(syn), None)
    assert doc.status == DocumentStatus.NEEDS_REVIEW
    assert any("subtotaal" in n for n in doc.processing_notes)


def test_csv_invoice_table(db, client_factory):
    invs = [electricity_invoice("A-1"), electricity_invoice("A-2", date(2026, 2, 1), date(2026, 2, 28))]
    doc = ingest_upload(db, client_factory(), "export.csv", invoices_to_csv(invs), None)
    assert doc.doc_type == DocumentType.INVOICE_TABLE
    assert doc.status == DocumentStatus.PROCESSED, doc.processing_notes
    rows = db.scalars(select(Invoice).where(Invoice.document_id == doc.id).order_by(Invoice.invoice_number)).all()
    assert [r.invoice_number for r in rows] == ["A-1", "A-2"]
    assert len(rows[0].lines) == 3
    assert rows[0].lines[0].source_row == 2
    assert rows[0].lines[0].extraction_method == ExtractionMethod.CSV


def test_xlsx_invoice_table_with_numeric_cells(db, client_factory):
    wb = Workbook()
    ws = wb.active
    ws.append(["Factuurnummer", "Factuurdatum", "Leverancier", "EAN", "Periode_start", "Periode_eind",
               "Omschrijving", "Hoeveelheid", "Eenheid", "Tarief", "Bedrag", "Subtotaal", "BTW_bedrag", "Totaal"])
    ws.append(["X-1", date(2026, 1, 31), "Eneco", EAN_1, date(2026, 1, 1), date(2026, 1, 31),
               "Levering gas", 1234.5, "m3", 0.95123, 1174.30, 1174.30, 246.60, 1420.90])
    buf = io.BytesIO()
    wb.save(buf)
    doc = ingest_upload(db, client_factory(), "export.xlsx", buf.getvalue(), None)
    inv = db.scalar(select(Invoice).where(Invoice.document_id == doc.id))
    line = inv.lines[0]
    assert line.quantity == D("1234.5") and line.unit_price == D("0.95123") and line.amount == D("1174.3")
    assert line.category == LineCategory.GAS_SUPPLY and line.unit == "m3"


def test_meter_data_csv(db, client_factory):
    csv_bytes = (
        "ean;meternummer;register;datum;stand;type;vermenigvuldigingsfactor\n"
        f"{EAN_1};E1;normaal;01-01-2026;12.000;werkelijk;1\n"
        f"{EAN_1};E1;normaal;31-01-2026;13.000;geschat;1\n"
    ).encode()
    doc = ingest_upload(db, client_factory(), "meter.csv", csv_bytes, None)
    assert doc.doc_type == DocumentType.METER_DATA
    readings = db.scalars(select(MeterReading).where(MeterReading.document_id == doc.id)
                          .order_by(MeterReading.reading_date)).all()
    assert [r.reading for r in readings] == [D("12000"), D("13000")]
    assert readings[1].reading_type == ReadingType.ESTIMATED
    assert readings[0].register == "NORMAL" and readings[0].source_row == 2


def test_amount_only_lines_do_not_capture_totals(db, client_factory):
    syn = electricity_invoice(extra_lines=[SynLine("Vermindering energiebelasting", None, None, None, D("-52.06"))])
    doc = ingest_upload(db, client_factory(), "x.pdf", render_invoice_pdf(syn), None)
    inv = db.scalar(select(Invoice).where(Invoice.document_id == doc.id))
    descs = [line.description.lower() for line in inv.lines]
    assert not any("totaal" in d or "btw" in d for d in descs)
    assert inv.lines[-1].category == LineCategory.TAX_REDUCTION


@pytest.mark.parametrize("name,content", [
    ("a.exe", b"MZ..."), ("a.pdf", b"not a pdf"), ("a.xlsx", b"PK\x03\x04garbage"), ("a.csv", b"\x00\x01"),
    ("a.pdf", b""), ("a.png", b"GIF89a"),
])
def test_upload_validation_rejects(db, client_factory, name, content):
    with pytest.raises(UploadRejected):
        ingest_upload(db, client_factory(), name, content, None)


def test_safe_filename():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("C:\\x\\factuur jan<>.pdf") == "factuur jan__.pdf"
