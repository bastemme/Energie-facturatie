"""All tools available to agents. Implemented tools have a handler; planned tools are declared so the
dashboard and permission model already know them."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.agents.tools import ToolSpec, register
from app.domain.enums import LeadStage, OutreachKind, OutreachStatus, TaskStatus
from app.domain.replies import classify_reply
from app.integrations.email.provider import get_email_provider
from app.integrations.web import contacts as web_contacts
from app.integrations.web.research import get_research_provider
from app.integrations.web.website import fetch_website
from app.models import InboundMessage, OutreachMessage, Prospect, RecoveryCase
from app.models.base import utcnow
from app.services import crm, operations, prospects
from app.services.analysis import run_analysis


def _search(sector_filters: list[str], area: str, limit: int, sector: str):
    provider = get_research_provider()
    return provider, provider.search_businesses(sector_filters, area, limit, sector)


register(ToolSpec(
    "web.search_businesses", "Zoekt bedrijven per sector en regio in een openbare bron (standaard OpenStreetMap).",
    "web.search", _search,
    summarize=lambda r: {"provider": r[0].id, "results": len(r[1]), "test_data": r[0].is_test_data},
))
register(ToolSpec(
    "web.fetch_website", "Haalt de openbare website van een bedrijf op en leest titel, omschrijving, KvK-nummer en "
    "signalen over meerdere vestigingen.", "web.fetch", fetch_website,
    summarize=lambda f: {"status": f.status_code, "kvk": bool(f.kvk_number), "multi_location": bool(f.multi_location)},
))
register(ToolSpec("prospects.find_duplicate", "Zoekt of een bedrijf al eerder is onderzocht (bron, domein, naam).",
                  "prospects.read", prospects.find_duplicate, needs_db=True,
                  summarize=lambda p: {"duplicate": p is not None}))
register(ToolSpec("prospects.save", "Slaat een onderzocht bedrijf op, met bron en onderbouwing.", "prospects.write",
                  prospects.save_prospect, needs_db=True, summarize=lambda p: {"prospect_id": p.id}))



# ---------------------------------------------------------------- sales pipeline


def _load_prospects(db, ids: list[str]):
    rows = db.scalars(select(Prospect).where(Prospect.id.in_(ids))).all()
    order = {pid: i for i, pid in enumerate(ids)}
    return sorted(rows, key=lambda p: order.get(p.id, 0))


def _unanswered(db, older_than_days: int):
    """Sent first e-mails without any reply, follow-up or stop signal, older than the waiting period."""
    cutoff = utcnow() - timedelta(days=older_than_days)
    sent = db.scalars(select(OutreachMessage).where(
        OutreachMessage.kind == OutreachKind.INITIAL, OutreachMessage.status == OutreachStatus.SENT,
        OutreachMessage.sent_at <= cutoff)).all()
    out = []
    for m in sent:
        p = db.get(Prospect, m.prospect_id)
        if p is None or p.do_not_contact or p.stage.rank >= LeadStage.RESPONDED.rank:
            continue
        replied = db.scalar(select(func.count()).select_from(InboundMessage).where(
            InboundMessage.prospect_id == p.id, InboundMessage.received_at >= m.sent_at))
        followed = db.scalar(select(func.count()).select_from(OutreachMessage).where(
            OutreachMessage.follow_up_of_id == m.id))
        if not replied and not followed:
            out.append(m)
    return out


register(ToolSpec("prospects.load", "Leest onderzochte bedrijven met hun contacten.", "prospects.read",
                  _load_prospects, needs_db=True, summarize=lambda r: {"prospects": len(r)}))
register(ToolSpec("prospects.qualify", "Legt de kwalificatie en de redenen vast en zet de leadstatus.",
                  "prospects.write", crm.qualify, needs_db=True,
                  summarize=lambda p: {"qualification": p.qualification.value if p.qualification else None}))
register(ToolSpec("web.find_contacts", "Leest de openbare contact-, team- en over-ons-pagina's van een bedrijf en "
                  "haalt genoemde beslissers en zakelijke e-mailadressen op, met bron.", "contacts.research",
                  web_contacts.find_contacts,
                  summarize=lambda r: {"pages": len(r.pages_read), "found": len(r.findings)}))
register(ToolSpec("contacts.save", "Legt een gevonden contact vast bij de lead, met bron en zekerheid.",
                  "contacts.research", crm.save_contact, needs_db=True,
                  summarize=lambda c: {"saved": c is not None}))
register(ToolSpec("outreach.draft_message", "Legt een concept-e-mail vast; die wacht op goedkeuring van een "
                  "medewerker.", "outreach.draft", crm.create_message, needs_db=True,
                  summarize=lambda m: {"message_id": m.id, "status": m.status.value}))
register(ToolSpec("outreach.load_message", "Leest een e-mail uit de outreach-wachtrij.", "leads.read",
                  lambda db, message_id: db.get(OutreachMessage, message_id), needs_db=True,
                  summarize=lambda m: {"found": m is not None}))
register(ToolSpec("outreach.find_unanswered", "Zoekt verstuurde e-mails zonder reactie na de wachttijd.",
                  "leads.read", _unanswered, needs_db=True, summarize=lambda r: {"unanswered": len(r)}))
def _send(to_email: str, to_name: str | None, subject: str, body: str, in_reply_to: str | None = None):
    return get_email_provider().send_email(to_email=to_email, to_name=to_name, subject=subject, body=body,
                                           in_reply_to=in_reply_to)


register(ToolSpec("email.send", "Verstuurt een goedgekeurde e-mail via de gekoppelde e-mailprovider (LIVE of MOCK).",
                  "email.send", _send, summarize=lambda d: {"live": d.live}))
register(ToolSpec("inbox.fetch", "Leest recente berichten uit de mailbox van de e-mailprovider (alleen lezen).",
                  "email.read", lambda limit=50: get_email_provider().get_inbox(limit),
                  summarize=lambda r: {"messages": len(r)}))
register(ToolSpec("inbox.register", "Legt een ontvangen bericht vast en koppelt het aan lead en contact.",
                  "email.read", crm.register_inbound, needs_db=True, summarize=lambda m: {"new": m is not None}))
register(ToolSpec("email.classify_reply", "Classificeert een antwoord met vaste regels (met de gevonden "
                  "formuleringen als reden).", "email.read", classify_reply,
                  summarize=lambda c: {"category": c.category.value, "confidence": c.confidence.value}))
register(ToolSpec("leads.apply_reply", "Werkt leadstatus, volgende actie en afmeldingen bij na een antwoord.",
                  "leads.write", crm.apply_classification, needs_db=True))

# ---------------------------------------------------------------- invoices, recovery, platform


def _requeue(db, task_ids: list[str], reason: str) -> int:
    from app.models import AgentTask

    n = 0
    for t in db.scalars(select(AgentTask).where(AgentTask.id.in_(task_ids))):
        t.status, t.error, t.not_before = TaskStatus.QUEUED, reason, None
        n += 1
    return n


register(ToolSpec("clients.load", "Leest een klant (alleen lopende opdrachten).", "clients.read",
                  lambda db, client_id: operations.active_client(db, client_id), needs_db=True,
                  summarize=lambda c: {"client": c.id}))
register(ToolSpec("invoices.intake", "Verwerkt nog niet uitgelezen documenten en meldt wat handwerk vraagt.",
                  "invoices.write", operations.intake, needs_db=True,
                  summarize=lambda r: {"processed_now": len(r.processed_now), "attention": len(r.attention),
                                       "invoices": r.invoices}))
register(ToolSpec("analysis.run", "Voert alle controleregels uit voor een klant.", "analysis.run",
                  lambda db, client: run_analysis(db, client, None), needs_db=True,
                  summarize=lambda r: {"findings": r.findings_total, "new": r.findings_new,
                                       "errors": len(r.errors or [])}))
register(ToolSpec("findings.audit", "Controleert bevindingen op bron, berekening en consistentie.", "findings.read",
                  operations.audit_client, needs_db=True,
                  summarize=lambda r: {"checked": r[1], "with_issues": sum(1 for _, i in r[0] if i)}))
register(ToolSpec("cases.prepare", "Maakt dossiers per leverancier van bevestigde bevindingen.", "cases.write",
                  operations.prepare_cases, needs_db=True, summarize=lambda r: {"cases": len(r[0])}))
register(ToolSpec("cases.load", "Leest een terugvorderingsdossier.", "cases.read",
                  lambda db, case_id: db.get(RecoveryCase, case_id), needs_db=True,
                  summarize=lambda c: {"found": c is not None}))
register(ToolSpec("claims.draft_letter", "Stelt de claimbrief aan de leverancier op (vaste sjabloon).",
                  "claims.draft", operations.draft_claim, needs_db=True,
                  summarize=lambda r: {"status_changed": r["status_changed"]}))
register(ToolSpec("claims.submit", "Registreert dat de claim is ingediend (alleen na goedkeuring).",
                  "claims.submit", operations.register_submission, needs_db=True))
register(ToolSpec("clients.status_update", "Stelt een statusupdate voor de klant op.", "clients.read",
                  operations.client_update, needs_db=True, summarize=lambda r: {"has_email": r is not None}))
register(ToolSpec("finance.metrics", "Ontvangen bedragen, succesvergoedingen en uitkeringen.", "finance.read",
                  operations.finance_report, needs_db=True, summarize=lambda r: {"sections": len(r["sections"])}))
register(ToolSpec("analytics.metrics", "Prestaties van pijplijn, agents en controleregels.", "analytics.read",
                  operations.analytics_report, needs_db=True, summarize=lambda r: {"sections": len(r["sections"])}))
register(ToolSpec("qa.review_output", "Toetst recente uitvoer van agents aan de werkregels.", "qa.review",
                  operations.qa_review, needs_db=True, summarize=lambda r: {"issues": r["issues"]}))
register(ToolSpec("tasks.find_stuck", "Zoekt taken die te lang op 'bezig' staan.", "tasks.read",
                  operations.stuck_tasks, needs_db=True, summarize=lambda r: {"stuck": len(r)}))
register(ToolSpec("tasks.requeue", "Zet vastgelopen taken terug in de wachtrij.", "tasks.create", _requeue,
                  needs_db=True, summarize=lambda n: {"requeued": n}))
register(ToolSpec("clients.needing_cases", "Klanten met bevestigde bevindingen die nog in geen dossier zitten.",
                  "findings.read", operations.clients_needing_cases, needs_db=True,
                  summarize=lambda r: {"clients": len(r)}))

# Planned tools — declared so permissions and the dashboard know them; not implemented yet.
for name, desc, perm in [
    ("kvk.lookup", "Controleert bedrijfsgegevens in het KvK Handelsregister (API-sleutel nodig).", "web.search"),
]:
    register(ToolSpec(name, desc, perm))
