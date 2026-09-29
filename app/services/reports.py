"""PDF reports (client report, supplier claim) and the claim package ZIP."""

from __future__ import annotations

import io
import json
import zipfile
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.domain.enums import Classification, ReviewStatus
from app.domain.money import format_eur, round_cents
from app.ingestion.storage import get_store
from app.models import Anomaly, Client, Document, Invoice, RecoveryCase
from app.models.base import utcnow
from app.services.cases import STATUS_LABELS_NL
from app.services.correspondence import ClaimItem, Letter
from app.services.metrics import Summary
from app.services.review import REVIEW_LABELS_NL

_styles = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=_styles["Heading1"], fontSize=16, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=_styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("body", parent=_styles["BodyText"], fontSize=9, leading=12)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#444444"))
NAVY = colors.HexColor("#1f3a5f")

DISCLAIMER = (
    "Mogelijke discrepanties zijn uitkomsten van een geautomatiseerde en/of handmatige controle en vormen geen "
    "vaststaande vordering. Alleen bedragen onder 'Daadwerkelijk teruggevorderd' zijn door de leverancier "
    "gecrediteerd of terugbetaald. Belastinggerelateerde bevindingen vereisen fiscale verificatie."
)


def _p(text: str, style=BODY) -> Paragraph:
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def _table(rows, widths, header=True) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8ccd2")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
    t.setStyle(TableStyle(style))
    return t


def _doc(buf, title: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm,
                             bottomMargin=18 * mm, title=title)


def client_report_pdf(client: Client, s: Summary, anomalies: list[Anomaly], cases: list[RecoveryCase],
                      operator_name: str) -> bytes:
    buf = io.BytesIO()
    story = [
        _p(f"Rapportage energiefactuurcontrole — {client.company_name}", H1),
        _p(f"Opgesteld door {operator_name} op {utcnow():%d-%m-%Y}", SMALL),
        Spacer(1, 6),
        _p("Samenvatting", H2),
        _table([
            ["Onderdeel", "Waarde"],
            ["Geanalyseerde facturen", str(s.invoices_analyzed)],
            ["Totale factuurwaarde (incl. btw)", format_eur(s.total_invoice_value)],
            ["Mogelijke discrepanties (nog te verifiëren)", format_eur(s.potential_discrepancies)],
            ["Bevestigde discrepanties (na beoordeling)", format_eur(s.confirmed_discrepancies)],
            ["Ingediend bij leverancier(s)", format_eur(s.claimed_amount)],
            ["Toegekend door leverancier(s)", format_eur(s.supplier_approved)],
            ["Daadwerkelijk teruggevorderd", format_eur(s.recovered)],
            [f"Succesvergoeding ({client.success_fee_percentage}% van teruggevorderd)", format_eur(s.success_fee)],
        ], [110 * mm, 60 * mm]),
        Spacer(1, 4),
        _p("Bedragen zijn excl. btw, tenzij anders vermeld. Overlappende bevindingen zijn niet dubbel geteld.", SMALL),
        _p("Bevindingen", H2),
    ]
    rows = [["Factuur", "Bevinding", "Type", "Zekerheid", "Status", "Mogelijk bedrag"]]
    for a in anomalies:
        rows.append([
            _p(a.invoice.invoice_number if a.invoice else "—", SMALL), _p(a.title, SMALL),
            "Afwijking" if a.classification == Classification.ANOMALY else "Mogelijke fout",
            a.confidence.value, REVIEW_LABELS_NL[a.review_status], format_eur(a.potential_recovery),
        ])
    if len(rows) == 1:
        rows.append(["—", "Geen bevindingen", "", "", "", ""])
    story.append(_table(rows, [24 * mm, 70 * mm, 22 * mm, 17 * mm, 20 * mm, 22 * mm]))
    if cases:
        story += [_p("Terugvorderingsdossiers", H2)]
        crow = [["Dossier", "Leverancier", "Status", "Geclaimd", "Toegekend", "Teruggevorderd"]]
        for c in cases:
            crow.append([c.reference, _p(c.supplier, SMALL), STATUS_LABELS_NL[c.status], format_eur(c.disputed_amount),
                         format_eur(c.confirmed_amount), format_eur(c.recovered_amount)])
        story.append(_table(crow, [26 * mm, 36 * mm, 34 * mm, 25 * mm, 25 * mm, 28 * mm]))
    story += [Spacer(1, 10), _p(DISCLAIMER, SMALL)]
    _doc(buf, f"Rapportage {client.company_name}").build(story)
    return buf.getvalue()


def claim_pdf(case: RecoveryCase, client: Client, letter: Letter, items: list[ClaimItem]) -> bytes:
    buf = io.BytesIO()
    story = [_p(letter.subject, H1), Spacer(1, 4)]
    story += [_p(par) for par in letter.body.split("\n\n")]
    story += [PageBreak(), _p(f"Specificatie — dossier {case.reference}", H1),
              _p(f"Klant: {client.company_name}" + (f" (KvK {client.kvk_number})" if client.kvk_number else "")
                 + f" · Leverancier: {case.supplier}", BODY), Spacer(1, 6)]
    rows = [["#", "Factuur", "Periode", "EAN", "Omschrijving", "Gefactureerd", "Verwacht", "Correctie"]]
    total = Decimal(0)
    for n, item in enumerate(items, 1):
        a, inv = item.anomaly, item.invoice
        money = a.unit == "EUR"
        rows.append([
            str(n), _p(inv.invoice_number if inv else "—", SMALL), _p(str(inv.period) if inv and inv.period else "—",
                                                                     SMALL),
            _p(inv.ean if inv and inv.ean else "—", SMALL), _p(a.title, SMALL),
            format_eur(a.actual_value) if money else f"{a.actual_value} {a.unit}",
            format_eur(a.expected_value) if money else f"{a.expected_value} {a.unit}",
            format_eur(a.potential_recovery),
        ])
        total += a.potential_recovery
    story.append(_table(rows, [6 * mm, 20 * mm, 26 * mm, 24 * mm, 44 * mm, 18 * mm, 18 * mm, 18 * mm]))
    story += [Spacer(1, 4), _p(f"Gevraagde correctie (niet dubbel geteld): {format_eur(case.disputed_amount)} "
                               "excl. btw", BODY)]
    for n, item in enumerate(items, 1):
        a = item.anomaly
        story += [_p(f"{n}. {a.title}", H2), _p(a.description), _p("Berekening:", BODY)]
        story += [_p(f"• {step}", SMALL) for step in a.calculation]
        story += [_p("Bronnen:", BODY)]
        story += [_p(f"• {e.get('label')}" + (f" — \"{e['source_text']}\"" if e.get("source_text") else ""), SMALL)
                  for e in a.evidence]
    _doc(buf, letter.subject).build(story)
    return buf.getvalue()


def case_json(case: RecoveryCase, client: Client, items: list[ClaimItem]) -> dict:
    def d(v):  # non-monetary values: canonical, backend-independent (Postgres pads NUMERIC scale)
        if v is None:
            return None
        return format(v.normalize(), "f") if isinstance(v, Decimal) else str(v)

    def m(v):  # money: always exactly 2 decimals
        return None if v is None else str(round_cents(v))

    return {
        "schema": "energy-recovery.case/v1",
        "generated_at": utcnow().isoformat(),
        "case": {
            "id": case.id, "reference": case.reference, "supplier": case.supplier, "status": case.status.value,
            "disputed_amount": m(case.disputed_amount), "confirmed_amount": m(case.confirmed_amount),
            "recovered_amount": m(case.recovered_amount), "success_fee_percentage": d(case.success_fee_percentage),
            "success_fee": m(case.success_fee), "created_at": case.created_at.isoformat() if case.created_at else None,
            "submitted_at": case.submitted_at.isoformat() if case.submitted_at else None,
            "closed_at": case.closed_at.isoformat() if case.closed_at else None,
        },
        "client": {"id": client.id, "company_name": client.company_name, "kvk_number": client.kvk_number},
        "items": [{
            "anomaly_id": i.anomaly.id, "rule_id": i.anomaly.rule_id, "rule_version": i.anomaly.rule_version,
            "title": i.anomaly.title, "description": i.anomaly.description, "reason": i.anomaly.reason,
            "classification": i.anomaly.classification.value, "confidence": i.anomaly.confidence.value,
            "review_status": i.anomaly.review_status.value, "unit": i.anomaly.unit,
            "actual": d(i.anomaly.actual_value), "expected": d(i.anomaly.expected_value),
            "difference": d(i.anomaly.difference), "potential_recovery": m(i.anomaly.potential_recovery),
            "calculation": i.anomaly.calculation, "evidence": i.anomaly.evidence,
            "invoice": None if i.invoice is None else {
                "id": i.invoice.id, "number": i.invoice.invoice_number, "document_id": i.invoice.document_id,
                "invoice_date": d(i.invoice.invoice_date), "period_start": d(i.invoice.billing_period_start),
                "period_end": d(i.invoice.billing_period_end), "ean": i.invoice.ean,
            },
        } for i in items],
        "events": [{"at": e.created_at.isoformat() if e.created_at else None, "type": e.event_type,
                    "from": e.from_status, "to": e.to_status, "note": e.note} for e in case.events],
    }


def claim_package_zip(case: RecoveryCase, client: Client, letter: Letter, items: list[ClaimItem],
                      documents: list[Document]) -> bytes:
    buf = io.BytesIO()
    store = get_store()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{case.reference}-verzoek-tot-correctie.pdf", claim_pdf(case, client, letter, items))
        zf.writestr(f"{case.reference}-dossier.json", json.dumps(case_json(case, client, items), indent=2,
                                                                  ensure_ascii=False))
        zf.writestr(f"{case.reference}-brief.txt", f"Onderwerp: {letter.subject}\n\n{letter.body}")
        for doc in documents:
            zf.writestr(f"bijlagen/{doc.id[:8]}-{doc.original_filename}", store.get(doc.storage_key))
    return buf.getvalue()


def reportable_anomalies(anomalies: list[Anomaly]) -> list[Anomaly]:
    keep = {ReviewStatus.OPEN, ReviewStatus.INVESTIGATING, ReviewStatus.INFO_REQUESTED, ReviewStatus.CONFIRMED}
    return [a for a in anomalies if not a.is_stale and a.review_status in keep]


def invoices_by_id(invoices: list[Invoice]) -> dict[str, Invoice]:
    return {i.id: i for i in invoices}
