"""Services behind the invoice, recovery and platform agents. They reuse the existing deterministic code
(document processing, detection, cases, correspondence, metrics); nothing here computes money differently.

Report-style results use one shape so every agent's output renders the same way on the task page:
    {"summary": str, "sections": [{"title": str, "columns": [str], "rows": [[str]]}]}
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import (
    CaseStatus,
    Classification,
    Confidence,
    DocumentStatus,
    DocumentType,
    OutreachStatus,
    ReviewStatus,
    TaskStatus,
)
from app.domain.money import ZERO, format_eur
from app.models import (
    AgentTask,
    Anomaly,
    CaseEvent,
    Client,
    Contact,
    Document,
    Invoice,
    OutreachMessage,
    Prospect,
    RecoveryCase,
)
from app.models.base import new_id, utcnow

OPEN_REVIEW = (ReviewStatus.OPEN, ReviewStatus.INVESTIGATING, ReviewStatus.INFO_REQUESTED)
INVOICE_DOCS = (DocumentType.INVOICE, DocumentType.CREDIT_NOTE, DocumentType.INVOICE_TABLE)


def section(title: str, columns: list[str], rows: list[list]) -> dict:
    return {"title": title, "columns": columns, "rows": [[("" if v is None else str(v)) for v in r] for r in rows]}


# ---------------------------------------------------------------- intake


STATUS_NL = {"PROCESSED": "Verwerkt", "NEEDS_REVIEW": "Controle nodig", "NEEDS_OCR": "Handmatige invoer nodig",
             "FAILED": "Niet verwerkt", "STORED": "Opgeslagen", "UPLOADED": "Nog niet verwerkt"}


@dataclass
class IntakeResult:
    processed_now: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    attention: list[Document] = field(default_factory=list)
    invoices: int = 0


def intake(db: Session, client: Client, document_ids: list[str] | None = None) -> IntakeResult:
    """Process documents that were stored but never read, and report what still needs a person."""
    from app.ingestion.pipeline import ReprocessBlocked, process_document

    q = select(Document).where(Document.client_id == client.id)
    if document_ids:
        q = q.where(Document.id.in_(document_ids))
    docs = db.scalars(q).all()
    out = IntakeResult()
    for d in docs:
        if d.status == DocumentStatus.UPLOADED:
            try:
                process_document(db, d)
                out.processed_now.append(d.id)
            except ReprocessBlocked:
                pass
    out.counts = dict(Counter(d.status.value for d in docs))
    out.attention = [d for d in docs if d.status in (DocumentStatus.NEEDS_REVIEW, DocumentStatus.NEEDS_OCR,
                                                      DocumentStatus.FAILED)]
    out.invoices = db.scalar(select(func.count()).select_from(Invoice).where(Invoice.client_id == client.id)) or 0
    return out


# ---------------------------------------------------------------- audit (internal check before human review)


def audit_anomaly(a: Anomaly) -> list[str]:
    """Checks that a finding is traceable and internally consistent. Returns the problems found (Dutch)."""
    issues = []
    if not a.evidence:
        issues.append("geen bronverwijzing (document/pagina)")
    elif not any(isinstance(e, dict) and (e.get("document_id") or e.get("page")) for e in a.evidence):
        issues.append("bronverwijzing zonder document of pagina")
    if not a.calculation:
        issues.append("geen berekening vastgelegd")
    if a.potential_recovery and a.potential_recovery > 0:
        if a.classification == Classification.ANOMALY:
            issues.append("signaal (geen bewijs) met een terugvorderingsbedrag")
        if a.unit == "EUR" and (a.actual_value is None or a.expected_value is None):
            issues.append("bedrag zonder gefactureerde én verwachte waarde")
        elif a.unit == "EUR" and a.difference is not None and a.actual_value is not None and \
                a.expected_value is not None and abs((a.actual_value - a.expected_value) - a.difference) > Decimal(
                    "0.02"):
            issues.append("verschil klopt niet met gefactureerd min verwacht")
    if a.confidence == Confidence.HIGH and a.extraction_confidence is not None and a.extraction_confidence < 0.9:
        issues.append(f"zekerheid 'hoog' terwijl de uitlezing {a.extraction_confidence:.0%} zeker is")
    if a.invoice_id is None and a.potential_recovery and a.potential_recovery > 0:
        issues.append("bedrag zonder gekoppelde factuur")
    return issues


def audit_client(db: Session, client: Client) -> tuple[list[tuple[Anomaly, list[str]]], int]:
    anomalies = db.scalars(select(Anomaly).where(
        Anomaly.client_id == client.id, Anomaly.is_stale.is_(False),
        Anomaly.review_status.in_((*OPEN_REVIEW, ReviewStatus.CONFIRMED)))).all()
    return [(a, audit_anomaly(a)) for a in anomalies], len(anomalies)


# ---------------------------------------------------------------- recovery (cases) & claims


def confirmed_unassigned(db: Session, client_id: str) -> list[Anomaly]:
    return db.scalars(select(Anomaly).where(
        Anomaly.client_id == client_id, Anomaly.review_status == ReviewStatus.CONFIRMED, Anomaly.case_id.is_(None),
        Anomaly.is_stale.is_(False), Anomaly.potential_recovery > 0)).all()


def prepare_cases(db: Session, client: Client) -> tuple[list[RecoveryCase], list[str]]:
    """One case per supplier from confirmed findings not yet in a case (existing case rules apply)."""
    from app.services.cases import CaseError, _supplier_of, create_case

    by_supplier: dict[str | None, list[Anomaly]] = {}
    for a in confirmed_unassigned(db, client.id):
        by_supplier.setdefault(_supplier_of(db, a), []).append(a)
    cases, skipped = [], []
    for supplier, items in by_supplier.items():
        if not supplier:
            skipped.append(f"{len(items)} bevinding(en) zonder leverancier op de factuur")
            continue
        try:
            cases.append(create_case(db, client, items, None, note="Voorbereid door de Recovery-agent"))
        except CaseError as exc:
            skipped.append(f"{supplier}: {exc}")
    return cases, skipped


def draft_claim(db: Session, case: RecoveryCase) -> dict:
    """Draft the supplier letter with the existing template; mark the case CLAIM_PREPARED once verified."""
    from app.config import get_settings
    from app.services.cases import transition
    from app.services.correspondence import build_items
    from app.services.reports import invoices_by_id

    client = db.get(Client, case.client_id)
    ids = [a.invoice_id for a in case.anomalies if a.invoice_id]
    invoices = db.scalars(select(Invoice).where(Invoice.id.in_(ids))).all() if ids else []
    from app.services.correspondence import generate_letter

    letter = generate_letter(case, client, build_items(case, invoices_by_id(list(invoices))),
                             get_settings().operator_name)
    case.events.append(CaseEvent(id=new_id(), event_type="CLAIM_DRAFTED", note="Claimbrief opgesteld door de "
                                 "Claims-agent", data={"subject": letter.subject, "body": letter.body}))
    moved = False
    if case.status == CaseStatus.VERIFIED:
        transition(db, case, CaseStatus.CLAIM_PREPARED, None, note="Claimbrief opgesteld")
        moved = True
    db.flush()
    return {"subject": letter.subject, "body": letter.body, "status_changed": moved}


def register_submission(db: Session, case: RecoveryCase, approver_note: str) -> None:
    from app.services.cases import CaseError, transition

    if case.status not in (CaseStatus.CLAIM_PREPARED, CaseStatus.CLIENT_APPROVAL):
        raise CaseError(f"Dossier staat op {case.status.value}; indienen kan vanaf 'Claim voorbereid'.")
    transition(db, case, CaseStatus.SUBMITTED, None, note=approver_note)


# ---------------------------------------------------------------- customer success


def client_update(db: Session, client: Client) -> dict | None:
    """Status e-mail for the client. Potential money is always called 'mogelijk'; only received money counts."""
    from app.config import get_settings
    from app.services.metrics import summary

    if not client.contact_email:
        return None
    s = summary(db, client.id)
    docs = db.scalars(select(Document).where(Document.client_id == client.id)).all()
    attention = [d for d in docs if d.status in (DocumentStatus.NEEDS_REVIEW, DocumentStatus.NEEDS_OCR,
                                                 DocumentStatus.FAILED)]
    cases = db.scalars(select(RecoveryCase).where(RecoveryCase.client_id == client.id)).all()
    from app.services.cases import STATUS_LABELS_NL

    lines = [f"Beste {client.contact_name or 'relatie'},", "",
             f"Hierbij de stand van zaken van de controle van uw energiefacturen ({client.company_name}).", "",
             f"- Facturen gecontroleerd: {s.invoices_analyzed}",
             f"- Mogelijke afwijkingen in onderzoek: {s.open_findings} (samen {format_eur(s.potential_discrepancies)};"
             " dit is nog geen vaststaand bedrag)",
             f"- Bevestigde afwijkingen: {s.confirmed_findings} ({format_eur(s.confirmed_discrepancies)})"]
    for c in cases:
        lines.append(f"- Dossier {c.reference} ({c.supplier}): {STATUS_LABELS_NL[c.status].lower()}")
    lines.append(f"- Daadwerkelijk ontvangen: {format_eur(s.recovered)}")
    if attention:
        lines += ["", f"{len(attention)} document(en) konden we niet volledig uitlezen. Heeft u deze als "
                      "doorzoekbare pdf of export, dan horen we dat graag."]
    lines += ["", "Heeft u vragen, antwoord dan gerust op deze e-mail.", "", "Met vriendelijke groet,", "",
              get_settings().outreach_from_name, get_settings().operator_name]
    return {"to": client.contact_email, "to_name": client.contact_name,
            "subject": f"Stand van zaken controle energiefacturen {client.company_name}", "body": "\n".join(lines)}


# ---------------------------------------------------------------- finance & analytics


def finance_report(db: Session) -> dict:
    from app.services.metrics import summary

    rows, total = [], {"recovered": ZERO, "fee": ZERO, "payout": ZERO, "potential": ZERO}
    for c in db.scalars(select(Client).order_by(Client.company_name)):
        s = summary(db, c.id)
        payout = s.recovered - s.success_fee
        rows.append([c.company_name, format_eur(s.potential_discrepancies), format_eur(s.confirmed_discrepancies),
                     format_eur(s.recovered), format_eur(s.success_fee), format_eur(payout)])
        total["recovered"] += s.recovered
        total["fee"] += s.success_fee
        total["payout"] += payout
        total["potential"] += s.potential_discrepancies
    missing_ref = db.scalars(select(RecoveryCase).where(RecoveryCase.recovered_amount > 0,
                                                       RecoveryCase.recovered_reference.is_(None))).all()
    to_invoice = db.scalars(select(RecoveryCase).where(RecoveryCase.status.in_(
        (CaseStatus.RECOVERED, CaseStatus.CLOSED)), RecoveryCase.success_fee > 0)).all()
    sections = [section("Per klant", ["Klant", "Mogelijk", "Bevestigd", "Ontvangen", "Succesvergoeding",
                                      "Uit te keren aan klant"], rows)]
    if to_invoice:
        sections.append(section("Succesvergoeding te factureren", ["Dossier", "Leverancier", "Ontvangen", "Fee",
                                                                    "Kenmerk ontvangst"],
                                [[c.reference, c.supplier, format_eur(c.recovered_amount), format_eur(c.success_fee),
                                  c.recovered_reference or "ONBEKEND"] for c in to_invoice]))
    return {"summary": f"Ontvangen {format_eur(total['recovered'])}, succesvergoeding {format_eur(total['fee'])}, "
                       f"uit te keren {format_eur(total['payout'])}. Mogelijk (niet vaststaand): "
                       f"{format_eur(total['potential'])}.",
            "recovered": str(total["recovered"]), "success_fee": str(total["fee"]), "payout": str(total["payout"]),
            "warnings": [f"Dossier {c.reference}: ontvangen bedrag zonder kenmerk" for c in missing_ref],
            "sections": sections}


def analytics_report(db: Session) -> dict:
    from app.services import business
    from app.services.metrics import operator_metrics

    k = business.kpis(db)
    funnel = business.funnel(db, k)
    m = operator_metrics(db)
    per_agent: dict[str, list[AgentTask]] = {}
    for t in db.scalars(select(AgentTask).order_by(AgentTask.created_at.desc()).limit(5000)):
        per_agent.setdefault(t.agent_id, []).append(t)
    agent_rows = []
    for agent_id, tasks in sorted(per_agent.items()):
        durations = [t.duration_ms for t in tasks if t.duration_ms is not None]
        avg = sum(durations) / len(durations) / 1000 if durations else None
        agent_rows.append([agent_id, len(tasks), sum(t.status == TaskStatus.COMPLETED for t in tasks),
                           sum(t.status == TaskStatus.FAILED for t in tasks),
                           f"{avg:.1f} s".replace(".", ",") if avg is not None else "—"])
    return {
        "summary": f"{k.leads} leads, {k.qualified} gekwalificeerd, {k.emails_sent} e-mails verstuurd "
                   f"({k.emails_mock} mock), {k.positive_responses} positieve reacties; teruggevorderd per 1.000 "
                   f"facturen: {format_eur(m.recovered_per_1000_invoices)}.",
        "sections": [
            section("Van lead tot geld", ["Stap", "Aantal", "Doorstroom"],
                    [[s.label, f"{s.count} {s.unit}", f"{s.conversion:.0%}" if s.conversion is not None else "—"]
                     for s in funnel]),
            section("Agents", ["Agent", "Taken", "Afgerond", "Mislukt", "Gem. duur"], agent_rows),
            section("Controleregels", ["Regel", "Bevindingen", "Bevestigd", "Afgewezen", "Precisie"],
                    [[r["rule_id"], r["total"], r["confirmed"], r["rejected"],
                      f"{r['precision']:.0%}" if r["precision"] is not None else "—"] for r in m.rule_stats]),
        ],
    }


# ---------------------------------------------------------------- QA


BANNED = ("ik hoop dat het goed met u gaat", "gegarandeerd", "garantie", "altijd besparing", "zeker weten",
          "bespaart u", "u betaalt te veel")


def qa_review(db: Session, days: int = 7) -> dict:
    """Check recent agent output against the working rules. Returns issues per item; changes nothing."""
    from app.services.outreach_copy import MAX_WORDS, PROPOSITION, core_text, word_count

    since = utcnow() - timedelta(days=days)
    issues: list[list[str]] = []
    checked = Counter()
    for m in db.scalars(select(OutreachMessage).where(OutreachMessage.created_at >= since)):
        checked["e-mails"] += 1
        text = m.body.lower()
        words = word_count(core_text(m.body))
        if m.kind.value == "INITIAL":
            if words > MAX_WORDS:
                issues.append(["E-mail", m.subject, f"{words} woorden (max {MAX_WORDS})", f"/app/outreach/{m.id}"])
            if PROPOSITION.split(".")[-2].strip().lower() not in text:
                issues.append(["E-mail", m.subject, "no-cure-no-pay-zin ontbreekt", f"/app/outreach/{m.id}"])
            if "afmelden" not in text:
                issues.append(["E-mail", m.subject, "afmeldregel ontbreekt", f"/app/outreach/{m.id}"])
        for b in BANNED:
            if b in text:
                issues.append(["E-mail", m.subject, f"verboden formulering: '{b}'", f"/app/outreach/{m.id}"])
        if m.status == OutreachStatus.SENT and m.decided_by_id is None:
            issues.append(["E-mail", m.subject, "verstuurd zonder vastgelegde goedkeuring", f"/app/outreach/{m.id}"])
    for c in db.scalars(select(Contact).where(Contact.created_at >= since)):
        checked["contacten"] += 1
        if not c.source_url:
            issues.append(["Contact", c.display_name, "geen bron", f"/app/leads/{c.prospect_id}"])
        if c.email and c.email_type == "PERSONAL_BUSINESS" and c.source != "manual" and c.source != "form" and \
                c.source_excerpt and c.email not in c.source_excerpt.lower() and c.source != "test":
            issues.append(["Contact", c.display_name, "e-mailadres staat niet in het bronfragment",
                           f"/app/leads/{c.prospect_id}"])
    for p in db.scalars(select(Prospect).where(Prospect.created_at >= since)):
        checked["leads"] += 1
        if p.source not in ("aanvraag",) and not p.evidence:
            issues.append(["Lead", p.company_name, "geen bron", f"/app/leads/{p.id}"])
        if p.relevance and any(not r.get("source") for r in p.relevance):
            issues.append(["Lead", p.company_name, "relevantie zonder bron", f"/app/leads/{p.id}"])
    for a in db.scalars(select(Anomaly).where(Anomaly.created_at >= since, Anomaly.is_stale.is_(False))):
        checked["bevindingen"] += 1
        for problem in audit_anomaly(a):
            issues.append(["Bevinding", a.title, problem, f"/app/anomalies/{a.id}"])
    summary = ", ".join(f"{n} {k}" for k, n in checked.items()) or "niets"
    return {"summary": f"Gecontroleerd (laatste {days} dagen): {summary}. {len(issues)} aandachtspunt(en).",
            "issues": len(issues), "checked": dict(checked),
            "sections": [section("Aandachtspunten", ["Soort", "Item", "Probleem", "Link"], issues)] if issues else []}


# ---------------------------------------------------------------- orchestration helpers


def stuck_tasks(db: Session, minutes: int = 30) -> list[AgentTask]:
    cutoff = utcnow() - timedelta(minutes=minutes)
    return db.scalars(select(AgentTask).where(AgentTask.status == TaskStatus.RUNNING,
                                              AgentTask.started_at < cutoff)).all()


def clients_needing_cases(db: Session) -> list[str]:
    return list(db.scalars(select(Anomaly.client_id).where(
        Anomaly.review_status == ReviewStatus.CONFIRMED, Anomaly.case_id.is_(None), Anomaly.is_stale.is_(False),
        Anomaly.potential_recovery > 0).distinct()).all())


def active_client(db: Session, client_id: str) -> Client:
    c = db.get(Client, client_id)
    if c is None:
        raise ValueError("Klant niet gevonden.")
    if c.engagement_ended_at is not None:
        raise ValueError("De opdracht voor deze klant is beëindigd.")
    return c

