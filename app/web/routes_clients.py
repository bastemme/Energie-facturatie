"""Clients, intake, documents, invoices (incl. manual correction), contracts, GDPR actions."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.db import get_db
from app.domain.enums import (
    Commodity,
    DocumentType,
    ExtractionMethod,
    InvoiceType,
    LeadStatus,
    LineCategory,
    ReviewStatus,
    Role,
)
from app.domain.fees import validate_fee_percentage
from app.domain.units import normalize_unit
from app.extraction.categories import classify_line
from app.extraction.pdf_text import render_page_png
from app.ingestion.pipeline import DuplicateDocument, ReprocessBlocked, ingest_upload, process_document
from app.ingestion.storage import get_store
from app.ingestion.validation import UploadRejected
from app.models import (
    Anomaly,
    Client,
    Contract,
    ContractPrice,
    Document,
    ExtractedValue,
    Invoice,
    InvoiceLine,
    Lead,
    MeterReading,
    RecoveryCase,
    User,
)
from app.models.base import utcnow
from app.services.analysis import run_analysis
from app.services.audit import audit
from app.services.metrics import summary
from app.services.privacy import erase_client, export_client_data
from app.services.reports import client_report_pdf, reportable_anomalies
from app.web.deps import attachment, client_ip, flash, form_date, form_decimal, redirect, render
from app.web.security import (
    get_client_for,
    get_owned,
    hash_password,
    require_admin,
    require_staff,
    require_user,
    verify_csrf,
)

router = APIRouter()
CONSENT_TEXT_VERSION = "machtiging-v1"


@router.get("/app")
def app_home(user: User = Depends(require_user)):
    if user.is_staff:
        return redirect("/app/admin")
    return redirect(f"/app/clients/{user.client_id}")


# ---------------------------------------------------------------- clients & intake


@router.get("/app/clients")
def clients_list(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    clients = db.scalars(select(Client).order_by(Client.company_name)).all()
    return render(request, "clients/list.html", user=user, clients=clients,
                  summaries={c.id: summary(db, c.id) for c in clients})


@router.get("/app/clients/new")
def client_new(request: Request, lead_id: str | None = None, user: User = Depends(require_staff),
               db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id) if lead_id else None
    return render(request, "clients/new.html", user=user, lead=lead, consent_version=CONSENT_TEXT_VERSION,
                  default_fee=get_settings().default_success_fee_percentage,
                  default_retention=get_settings().default_retention_days)


@router.post("/app/clients/new", dependencies=[Depends(verify_csrf)])
async def client_create(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    form = await request.form()
    errors = []
    name = str(form.get("company_name", "")).strip()
    if not name:
        errors.append("Bedrijfsnaam is verplicht.")
    kvk = "".join(ch for ch in str(form.get("kvk_number", "")) if ch.isdigit())
    if kvk and len(kvk) != 8:
        errors.append("KvK-nummer moet 8 cijfers zijn.")
    fee = spend = None
    try:
        fee = form_decimal(str(form.get("success_fee_percentage", "")))
        if fee is None:
            errors.append("Succesvergoeding (%) is verplicht — leg het afgesproken percentage vast.")
        else:
            validate_fee_percentage(fee)
        spend = form_decimal(str(form.get("approx_annual_energy_spend", "")))
    except ValueError as exc:
        errors.append(str(exc))
    consent = form.get("consent_given") == "on"
    if not consent:
        errors.append("Leg de machtiging/toestemming van de klant vast voordat facturen worden verwerkt.")
    if errors:
        return render(request, "clients/new.html", status_code=400, user=user, errors=errors, lead=None,
                      form=form, consent_version=CONSENT_TEXT_VERSION)
    locations = str(form.get("number_of_locations", "")).strip()
    client = Client(
        company_name=name[:200], kvk_number=kvk or None,
        contact_name=str(form.get("contact_name", "")).strip()[:200] or None,
        contact_email=str(form.get("contact_email", "")).strip()[:254] or None,
        contact_phone=str(form.get("contact_phone", "")).strip()[:50] or None,
        number_of_locations=int(locations) if locations.isdigit() else None,
        approx_annual_energy_spend=spend,
        suppliers=str(form.get("suppliers", "")).strip()[:500] or None,
        invoice_period_description=str(form.get("invoice_period_description", "")).strip()[:200] or None,
        contract_information=str(form.get("contract_information", "")).strip()[:5000] or None,
        success_fee_percentage=fee, consent_given=True, consent_text_version=CONSENT_TEXT_VERSION,
        consent_given_at=utcnow(), consent_given_by=str(form.get("consent_given_by", "")).strip()[:200] or None,
        vat_deductible=form.get("vat_deductible") == "on",
        ai_processing_allowed=form.get("ai_processing_allowed") == "on",
        retention_days=int(str(form.get("retention_days") or get_settings().default_retention_days)),
    )
    db.add(client)
    db.flush()
    lead_id = form.get("lead_id")
    if lead_id and (lead := db.get(Lead, str(lead_id))):
        lead.converted_client_id = client.id
        lead.status = LeadStatus.CONVERTED
    audit(db, "client.created", user=user, client_id=client.id, object_type="client", object_id=client.id)
    db.commit()
    flash(request, "Klant aangemaakt.", "success")
    return redirect(f"/app/clients/{client.id}")


@router.get("/app/clients/{client_id}")
def client_detail(client_id: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    client = get_client_for(db, user, client_id)
    docs = db.scalars(select(Document).where(Document.client_id == client.id)
                      .order_by(Document.created_at.desc())).all()
    invoices = db.scalars(select(Invoice).where(Invoice.client_id == client.id)
                          .order_by(Invoice.billing_period_start, Invoice.invoice_number)).all()
    anomalies = db.scalars(select(Anomaly).where(Anomaly.client_id == client.id, Anomaly.is_stale.is_(False))
                           .order_by(Anomaly.potential_recovery.desc())).all()
    if not user.is_staff:  # clients see reviewed outcomes, not the raw detection queue
        anomalies = [a for a in anomalies if a.review_status == ReviewStatus.CONFIRMED]
    cases = db.scalars(select(RecoveryCase).where(RecoveryCase.client_id == client.id)
                       .order_by(RecoveryCase.created_at.desc())).all()
    contracts = db.scalars(select(Contract).where(Contract.client_id == client.id)).all()
    users = db.scalars(select(User).where(User.client_id == client.id)).all() if user.is_staff else []
    return render(request, "clients/detail.html", user=user, client=client, docs=docs, invoices=invoices,
                  anomalies=anomalies, cases=cases, contracts=contracts, users=users, s=summary(db, client.id),
                  doc_types=[t for t in DocumentType if t not in (DocumentType.UNKNOWN,)])


@router.post("/app/clients/{client_id}/upload", dependencies=[Depends(verify_csrf)])
async def upload(client_id: str, request: Request, files: list[UploadFile] = File(...),
                 declared_type: str = Form(""), user: User = Depends(require_user), db: Session = Depends(get_db)):
    client = get_client_for(db, user, client_id)
    if not client.consent_given:
        raise HTTPException(status_code=403, detail="Er is nog geen machtiging vastgelegd voor deze klant.")
    declared = DocumentType(declared_type) if declared_type in DocumentType.__members__ else None
    max_bytes = get_settings().max_upload_bytes
    ok, failed = 0, []
    for f in files[:200]:
        content = await f.read(max_bytes + 1)
        try:
            doc = ingest_upload(db, client, f.filename, content, user, declared_type=declared)
            audit(db, "document.uploaded", user=user, client_id=client.id, object_type="document",
                  object_id=doc.id, ip=client_ip(request))
            db.commit()
            ok += 1
        except (UploadRejected, DuplicateDocument) as exc:
            db.rollback()
            failed.append(f"{f.filename}: {exc}")
    if ok:
        flash(request, f"{ok} document(en) verwerkt.", "success")
    for msg in failed:
        flash(request, msg, "error")
    return redirect(f"/app/clients/{client.id}#documenten")


@router.post("/app/clients/{client_id}/analyse", dependencies=[Depends(verify_csrf)])
def analyse(client_id: str, request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    client = get_client_for(db, user, client_id)
    run = run_analysis(db, client, user)
    audit(db, "analysis.run", user=user, client_id=client.id, object_type="analysis_run", object_id=run.id)
    db.commit()
    msg = f"Analyse uitgevoerd: {run.findings_total} bevindingen, waarvan {run.findings_new} nieuw."
    if run.errors:
        flash(request, f"{len(run.errors)} controleregel(s) gaven een fout; zie beheer.", "error")
    flash(request, msg, "success")
    return redirect(f"/app/clients/{client.id}#bevindingen")


@router.get("/app/clients/{client_id}/report.pdf")
def client_report(client_id: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    client = get_client_for(db, user, client_id)
    anomalies = db.scalars(select(Anomaly).where(Anomaly.client_id == client.id)
                           .options(selectinload(Anomaly.invoice)).order_by(Anomaly.potential_recovery.desc())).all()
    anomalies = reportable_anomalies(anomalies)
    if not user.is_staff:
        anomalies = [a for a in anomalies if a.review_status == ReviewStatus.CONFIRMED]
    cases = db.scalars(select(RecoveryCase).where(RecoveryCase.client_id == client.id)).all()
    pdf = client_report_pdf(client, summary(db, client.id), anomalies, cases, get_settings().operator_name)
    audit(db, "report.client_pdf", user=user, client_id=client.id, object_type="client", object_id=client.id,
          ip=client_ip(request))
    db.commit()
    return Response(pdf, media_type="application/pdf",
                    headers=attachment(f"rapportage-{client.id[:8]}.pdf"))


@router.post("/app/clients/{client_id}/users", dependencies=[Depends(verify_csrf)])
def add_client_user(client_id: str, request: Request, email: str = Form(...), full_name: str = Form(""),
                    password: str = Form(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    client = get_client_for(db, user, client_id)
    email = email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        flash(request, "Dit e-mailadres is al in gebruik.", "error")
        return redirect(f"/app/clients/{client.id}#gebruikers")
    try:
        pw = hash_password(password)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/app/clients/{client.id}#gebruikers")
    new = User(email=email, full_name=full_name or None, password_hash=pw, role=Role.CLIENT, client_id=client.id)
    db.add(new)
    db.flush()
    audit(db, "user.created", user=user, client_id=client.id, object_type="user", object_id=new.id)
    db.commit()
    flash(request, "Klantgebruiker aangemaakt.", "success")
    return redirect(f"/app/clients/{client.id}#gebruikers")


@router.get("/app/clients/{client_id}/export.json")
def export_json(client_id: str, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    client = get_client_for(db, user, client_id)
    data = export_client_data(db, client)
    audit(db, "client.exported", user=user, client_id=client.id, object_type="client", object_id=client.id,
          ip=client_ip(request))
    db.commit()
    return Response(json.dumps(data, indent=2, ensure_ascii=False), media_type="application/json",
                    headers=attachment(f"export-{client.id[:8]}.json"))


@router.post("/app/clients/{client_id}/end-engagement", dependencies=[Depends(verify_csrf)])
def end_engagement(client_id: str, request: Request, user: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    client = get_client_for(db, user, client_id)
    client.engagement_ended_at = utcnow()
    audit(db, "client.engagement_ended", user=user, client_id=client.id, object_type="client", object_id=client.id)
    db.commit()
    flash(request, f"Opdracht beëindigd. Gegevens worden na {client.retention_days} dagen verwijderd.", "success")
    return redirect(f"/app/clients/{client.id}")


@router.post("/app/clients/{client_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_client(client_id: str, request: Request, confirm_name: str = Form(...), reason: str = Form(...),
                  user: User = Depends(require_admin), db: Session = Depends(get_db)):
    client = get_client_for(db, user, client_id)
    if confirm_name.strip() != client.company_name:
        flash(request, "Bevestiging klopt niet; typ de exacte bedrijfsnaam.", "error")
        return redirect(f"/app/clients/{client.id}#privacy")
    erase_client(db, client, user, reason or "verzoek")
    db.commit()
    flash(request, "Klant en alle bijbehorende gegevens zijn permanent verwijderd.", "success")
    return redirect("/app/clients")


# ---------------------------------------------------------------- documents


@router.get("/app/documents/{doc_id}")
def document_detail(doc_id: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    doc = get_owned(db, Document, doc_id, user)
    invoices = db.scalars(select(Invoice).where(Invoice.document_id == doc.id)).all()
    readings = db.scalars(select(MeterReading).where(MeterReading.document_id == doc.id)
                          .order_by(MeterReading.reading_date).limit(500)).all()
    return render(request, "documents/detail.html", user=user, doc=doc, invoices=invoices, readings=readings,
                  client=db.get(Client, doc.client_id))


@router.get("/app/documents/{doc_id}/download")
def document_download(doc_id: str, request: Request, user: User = Depends(require_user),
                      db: Session = Depends(get_db)):
    doc = get_owned(db, Document, doc_id, user)
    content = get_store().get(doc.storage_key)
    audit(db, "document.downloaded", user=user, client_id=doc.client_id, object_type="document", object_id=doc.id,
          ip=client_ip(request))
    db.commit()
    return Response(content, media_type=doc.mime_type,
                    headers=attachment(f"{doc.original_filename}"))


@router.get("/app/documents/{doc_id}/page/{page}.png")
def document_page(doc_id: str, page: int, request: Request, hl: str | None = None,
                  user: User = Depends(require_user), db: Session = Depends(get_db)):
    doc = get_owned(db, Document, doc_id, user)
    if doc.mime_type != "application/pdf":
        raise HTTPException(status_code=404)
    highlights = []
    if hl:
        try:
            parts = [float(x) for x in hl.split(",")]
            if len(parts) % 4 == 0:
                highlights = [parts[i:i + 4] for i in range(0, len(parts), 4)][:20]
        except ValueError:
            highlights = []
    try:
        png = render_page_png(get_store().get(doc.storage_key), page, highlights)
    except ValueError as exc:
        raise HTTPException(status_code=404) from exc
    audit(db, "document.page_viewed", user=user, client_id=doc.client_id, object_type="document", object_id=doc.id)
    db.commit()
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/app/documents/{doc_id}/view/{page}")
def document_page_view(doc_id: str, page: int, request: Request, hl: str | None = None, label: str | None = None,
                       user: User = Depends(require_user), db: Session = Depends(get_db)):
    doc = get_owned(db, Document, doc_id, user)
    return render(request, "documents/page.html", user=user, doc=doc, page=page, hl=hl or "", label=label)


@router.post("/app/documents/{doc_id}/reprocess", dependencies=[Depends(verify_csrf)])
def document_reprocess(doc_id: str, request: Request, declared_type: str = Form(""),
                       user: User = Depends(require_staff), db: Session = Depends(get_db)):
    doc = get_owned(db, Document, doc_id, user)
    declared = DocumentType(declared_type) if declared_type in DocumentType.__members__ else None
    try:
        process_document(db, doc, declared_type=declared)
        audit(db, "document.reprocessed", user=user, client_id=doc.client_id, object_type="document", object_id=doc.id)
        db.commit()
        flash(request, "Document opnieuw verwerkt.", "success")
    except ReprocessBlocked as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return redirect(f"/app/documents/{doc.id}")


# ---------------------------------------------------------------- invoices (review & manual correction)


@router.post("/app/invoices/new", dependencies=[Depends(verify_csrf)])
def invoice_new(request: Request, client_id: str = Form(...), document_id: str = Form(""),
                user: User = Depends(require_staff), db: Session = Depends(get_db)):
    client = get_client_for(db, user, client_id)
    doc = get_owned(db, Document, document_id, user) if document_id else None
    inv = Invoice(client_id=client.id, document_id=doc.id if doc else None, extraction_confidence=1.0)
    db.add(inv)
    db.flush()
    audit(db, "invoice.created_manual", user=user, client_id=client.id, object_type="invoice", object_id=inv.id)
    db.commit()
    return redirect(f"/app/invoices/{inv.id}")


@router.get("/app/invoices/{invoice_id}")
def invoice_detail(invoice_id: str, request: Request, user: User = Depends(require_user),
                   db: Session = Depends(get_db)):
    inv = get_owned(db, Invoice, invoice_id, user)
    provenance = {ev.field_name: ev for ev in db.scalars(select(ExtractedValue)
                                                         .where(ExtractedValue.invoice_id == inv.id)).all()}
    anomalies = db.scalars(select(Anomaly).where(Anomaly.invoice_id == inv.id, Anomaly.is_stale.is_(False))).all()
    if not user.is_staff:
        anomalies = [a for a in anomalies if a.review_status == ReviewStatus.CONFIRMED]
    doc = db.get(Document, inv.document_id) if inv.document_id else None
    readings = db.scalars(select(MeterReading).where(MeterReading.invoice_id == inv.id)).all()
    return render(request, "invoices/detail.html", user=user, inv=inv, provenance=provenance, anomalies=anomalies,
                  doc=doc, readings=readings, categories=list(LineCategory), commodities=list(Commodity),
                  invoice_types=list(InvoiceType))


_HEADER_FIELDS = {
    "supplier": str, "invoice_number": str, "corrects_invoice_number": str, "ean": str, "meter_number": str,
    "invoice_date": "date", "billing_period_start": "date", "billing_period_end": "date",
    "subtotal_excl_vat": "dec", "vat_rate": "dec", "vat_amount": "dec", "total_incl_vat": "dec",
}


@router.post("/app/invoices/{invoice_id}", dependencies=[Depends(verify_csrf)])
async def invoice_update(invoice_id: str, request: Request, user: User = Depends(require_staff),
                         db: Session = Depends(get_db)):
    inv = get_owned(db, Invoice, invoice_id, user)
    form = await request.form()
    changed: list[str] = []
    try:
        for name, kind in _HEADER_FIELDS.items():
            raw = str(form.get(name, "")).strip()
            value = (form_date(raw) if kind == "date" else form_decimal(raw) if kind == "dec" else (raw or None))
            if value != getattr(inv, name):
                setattr(inv, name, value)
                changed.append(name)
                if inv.document_id:  # provenance: this value now comes from manual correction
                    db.add(ExtractedValue(document_id=inv.document_id, invoice_id=inv.id, field_name=name,
                                          value=None if value is None else str(value), confidence=1.0,
                                          method=ExtractionMethod.MANUAL))
        for name, enum in (("invoice_type", InvoiceType), ("commodity", Commodity)):
            raw = str(form.get(name, ""))
            if raw in enum.__members__ and getattr(inv, name) != enum(raw):
                setattr(inv, name, enum(raw))
                changed.append(name)
        for line in list(inv.lines):
            prefix = f"line-{line.id}-"
            if form.get(prefix + "delete") == "on":
                inv.lines.remove(line)
                changed.append("line.delete")
                continue
            if _apply_line(line, form, prefix):
                changed.append("line.update")
        if str(form.get("new-description", "")).strip():
            line = InvoiceLine(position=len(inv.lines), extraction_method=ExtractionMethod.MANUAL, confidence=1.0,
                               description="")
            _apply_line(line, form, "new-")
            inv.lines.append(line)
            changed.append("line.add")
    except ValueError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return redirect(f"/app/invoices/{invoice_id}")
    if inv.billing_period_start and inv.billing_period_end and inv.billing_period_start > inv.billing_period_end:
        db.rollback()
        flash(request, "Einddatum ligt vóór de begindatum.", "error")
        return redirect(f"/app/invoices/{invoice_id}")
    if changed:
        inv.verified_at = None  # any change requires re-verification
        inv.verified_by_id = None
        audit(db, "invoice.corrected", user=user, client_id=inv.client_id, object_type="invoice", object_id=inv.id,
              details={"fields": sorted(set(changed))})
    db.commit()
    flash(request, "Wijzigingen opgeslagen." if changed else "Geen wijzigingen.", "success")
    return redirect(f"/app/invoices/{inv.id}")


def _apply_line(line: InvoiceLine, form, prefix: str) -> bool:
    before = (line.description, line.category, line.quantity, line.unit, line.unit_price, line.amount, line.vat_rate)
    description = str(form.get(prefix + "description", line.description)).strip()[:500]
    unit = normalize_unit(str(form.get(prefix + "unit", "")).strip() or None)
    raw_cat = str(form.get(prefix + "category", ""))
    category = LineCategory(raw_cat) if raw_cat in LineCategory.__members__ else classify_line(description, unit)[0]
    line.description = description
    line.category = category
    line.unit = unit
    line.quantity = form_decimal(str(form.get(prefix + "quantity", "")))
    line.unit_price = form_decimal(str(form.get(prefix + "unit_price", "")))
    line.amount = form_decimal(str(form.get(prefix + "amount", "")))
    line.vat_rate = form_decimal(str(form.get(prefix + "vat_rate", "")))
    after = (line.description, line.category, line.quantity, line.unit, line.unit_price, line.amount, line.vat_rate)
    if before != after:
        line.extraction_method = ExtractionMethod.MANUAL
        line.confidence = 1.0
        line.category_confidence = 1.0
        return True
    return False


@router.post("/app/invoices/{invoice_id}/verify", dependencies=[Depends(verify_csrf)])
def invoice_verify(invoice_id: str, request: Request, user: User = Depends(require_staff),
                   db: Session = Depends(get_db)):
    inv = get_owned(db, Invoice, invoice_id, user)
    inv.verified_at = utcnow()
    inv.verified_by_id = user.id
    inv.extraction_confidence = 1.0
    audit(db, "invoice.verified", user=user, client_id=inv.client_id, object_type="invoice", object_id=inv.id)
    db.commit()
    flash(request, "Factuurgegevens gemarkeerd als geverifieerd tegen het brondocument. Voer de analyse opnieuw uit.",
          "success")
    return redirect(f"/app/invoices/{inv.id}")


# ---------------------------------------------------------------- contracts


@router.post("/app/clients/{client_id}/contracts", dependencies=[Depends(verify_csrf)])
def contract_create(client_id: str, request: Request, supplier: str = Form(...), start_date: str = Form(...),
                    end_date: str = Form(""), commodity: str = Form("ELECTRICITY"), contract_reference: str = Form(""),
                    ean: str = Form(""), document_id: str = Form(""), indexation: str = Form(""),
                    relevant_conditions: str = Form(""), user: User = Depends(require_staff),
                    db: Session = Depends(get_db)):
    client = get_client_for(db, user, client_id)
    try:
        start, end = form_date(start_date), form_date(end_date)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/app/clients/{client.id}#contracten")
    if start is None or (end and end < start):
        flash(request, "Ongeldige contractperiode.", "error")
        return redirect(f"/app/clients/{client.id}#contracten")
    doc = get_owned(db, Document, document_id, user) if document_id else None
    c = Contract(client_id=client.id, supplier=supplier.strip()[:200], start_date=start, end_date=end,
                 commodity=Commodity(commodity) if commodity in Commodity.__members__ else Commodity.ELECTRICITY,
                 contract_reference=contract_reference.strip()[:100] or None,
                 ean="".join(ch for ch in ean if ch.isdigit()) or None, document_id=doc.id if doc else None,
                 indexation=indexation.strip() or None, relevant_conditions=relevant_conditions.strip() or None)
    db.add(c)
    db.flush()
    audit(db, "contract.created", user=user, client_id=client.id, object_type="contract", object_id=c.id)
    db.commit()
    return redirect(f"/app/contracts/{c.id}")


@router.get("/app/contracts/{contract_id}")
def contract_detail(contract_id: str, request: Request, user: User = Depends(require_user),
                    db: Session = Depends(get_db)):
    c = get_owned(db, Contract, contract_id, user)
    return render(request, "contracts/detail.html", user=user, c=c, client=db.get(Client, c.client_id),
                  categories=list(LineCategory))


@router.post("/app/contracts/{contract_id}/prices", dependencies=[Depends(verify_csrf)])
def contract_price_add(contract_id: str, request: Request, category: str = Form(...), unit: str = Form(...),
                       price: str = Form(...), valid_from: str = Form(...), valid_to: str = Form(""),
                       source_page: str = Form(""), source_text: str = Form(""), user: User = Depends(require_staff),
                       db: Session = Depends(get_db)):
    c = get_owned(db, Contract, contract_id, user)
    try:
        p = form_decimal(price)
        vf, vt = form_date(valid_from), form_date(valid_to)
        if p is None or vf is None or category not in LineCategory.__members__:
            raise ValueError("Categorie, prijs en ingangsdatum zijn verplicht.")
        if vt and vt < vf:
            raise ValueError("Einddatum ligt vóór de ingangsdatum.")
    except ValueError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/app/contracts/{c.id}")
    c.prices.append(ContractPrice(category=LineCategory(category), unit=normalize_unit(unit) or unit, price=p,
                                  valid_from=vf, valid_to=vt,
                                  source_page=int(source_page) if source_page.isdigit() else None,
                                  source_text=source_text.strip()[:1000] or None))
    c.verified_at = None
    c.verified_by_id = None
    audit(db, "contract.price_added", user=user, client_id=c.client_id, object_type="contract", object_id=c.id)
    db.commit()
    return redirect(f"/app/contracts/{c.id}")


@router.post("/app/contracts/{contract_id}/prices/{price_id}/delete", dependencies=[Depends(verify_csrf)])
def contract_price_delete(contract_id: str, price_id: str, request: Request, user: User = Depends(require_staff),
                          db: Session = Depends(get_db)):
    c = get_owned(db, Contract, contract_id, user)
    for p in list(c.prices):
        if p.id == price_id:
            c.prices.remove(p)
            c.verified_at = None
    audit(db, "contract.price_deleted", user=user, client_id=c.client_id, object_type="contract", object_id=c.id)
    db.commit()
    return redirect(f"/app/contracts/{c.id}")


@router.post("/app/contracts/{contract_id}/verify", dependencies=[Depends(verify_csrf)])
def contract_verify(contract_id: str, request: Request, user: User = Depends(require_staff),
                    db: Session = Depends(get_db)):
    c = get_owned(db, Contract, contract_id, user)
    if not c.prices:
        flash(request, "Voeg eerst contractprijzen toe.", "error")
        return redirect(f"/app/contracts/{c.id}")
    c.verified_at = utcnow()
    c.verified_by_id = user.id
    audit(db, "contract.verified", user=user, client_id=c.client_id, object_type="contract", object_id=c.id)
    db.commit()
    flash(request, "Contractprijzen geverifieerd tegen het contract.", "success")
    return redirect(f"/app/contracts/{c.id}")
