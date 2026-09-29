"""Lead generation workflow: research → qualify → contacts → e-mail draft → approval → send → reply →
classification → lead update. Uses the test research source and the MOCK e-mail provider (no network)."""

import smtplib

import pytest
from sqlalchemy import func, select

from app.agents import registry
from app.agents.pipeline import qualify_rules
from app.agents.queue import claim_next, enqueue
from app.agents.runner import execute
from app.domain.enums import (
    Confidence,
    LeadStage,
    OutreachKind,
    OutreachStatus,
    Qualification,
    ReplyCategory,
    Role,
    TaskStatus,
)
from app.domain.replies import classify_reply
from app.integrations.email.provider import (
    EmailError,
    EmailNotConfigured,
    MockEmailProvider,
    SmtpImapProvider,
    parse_message,
)
from app.integrations.web.contacts import extract_contacts
from app.models import (
    AgentApproval,
    AgentTask,
    Contact,
    InboundMessage,
    LeadTask,
    OutreachMessage,
    Prospect,
    ProspectEvent,
    Suppression,
    User,
)
from app.services import crm, outreach_copy

# ---------------------------------------------------------------- helpers


@pytest.fixture
def agents(db):
    registry.sync_agents(db)
    return db


@pytest.fixture
def staff(db):
    u = User(email="ops@fs.nl", password_hash="x", role=Role.ADMIN)
    db.add(u)
    db.commit()
    return u


def run_all(db, limit=50):
    """Run the queue until it is empty, like the background runner does."""
    done = 0
    while done < limit and (task := claim_next(db)) is not None:
        execute(db, task)
        done += 1
    return done


def research(db, target=6, workflow="lead_generation"):
    t = enqueue(db, "lead_researcher", "research_prospects",
                {"preset": "energy_intensive", "country": "NL", "target": target, "require_website": True},
                title="test", workflow_id=workflow)
    db.commit()
    return t


def lead(db, name="Staal BV", website="https://staal.example", sector="manufacturing", score=75, **kw):
    p = Prospect(company_name=name, sector=sector, website=website, domain=website.split("//")[1] if website else None,
                 city="Tilburg", fit_score=score, fit_reasons=[], source="test", evidence=[], stage=LeadStage.NEW,
                 **kw)
    db.add(p)
    db.flush()
    return p


def contact(db, p, email="j.jansen@staal.example", name="Jan Jansen", role="Financieel manager"):
    return crm.save_contact(db, p, full_name=name, role=role, role_category="FINANCE", email=email,
                            email_type="PERSONAL_BUSINESS", linkedin_url=None, source="website",
                            source_url="https://staal.example/team", excerpt=f"{name} – {role}",
                            confidence=Confidence.HIGH)


def pending_message(db, p, c):
    draft = outreach_copy.initial_email(company=p.company_name, sector=p.sector, sector_label="Productie",
                                        city=p.city, multi_location_text=None, contact_name=c.full_name,
                                        contact_role=c.role, generic_mailbox=False)
    return crm.create_message(db, p, c, subject=draft.subject, body=draft.body,
                              personalization=draft.personalization)


# ---------------------------------------------------------------- lead creation & qualification


def test_lead_research_creates_sourced_leads_spread_over_industries(agents):
    db = agents
    task = research(db, target=6, workflow=None)
    run_all(db)
    db.refresh(task)
    assert task.status == TaskStatus.COMPLETED, task.error
    assert task.output["saved"] == 6 and task.progress_done == 6 and task.progress_total == 6
    leads = db.scalars(select(Prospect)).all()
    assert len({p.sector for p in leads}) >= 3  # round robin over the energy-intensive industries
    for p in leads:
        assert p.stage == LeadStage.NEW and p.evidence and p.relevance
        assert all(r["source"] for r in p.relevance)  # every "why relevant" statement has a source
        assert "onbekend" in p.relevance[0]["text"]  # no claims about actual energy use


def test_qualification_rules_are_explained():
    strong = Prospect(company_name="A", sector="food", fit_score=72, source="x", website="https://a.nl")
    grade, reasons = qualify_rules(strong)
    assert grade == Qualification.STRONG and "72" in reasons[0]
    branch = Prospect(company_name="B", sector="food", fit_score=72, source="x", brand="Keten", website="https://b.nl")
    assert qualify_rules(branch)[0] == Qualification.GOOD
    multi = Prospect(company_name="C", sector="food", fit_score=60, source="x", locations_count=4, website="https://c")
    assert qualify_rules(multi)[0] == Qualification.STRONG
    weak = Prospect(company_name="D", sector="food", fit_score=45, source="x")
    assert qualify_rules(weak)[0] == Qualification.WEAK
    inbound = Prospect(company_name="E", sector="unknown", fit_score=0, source="aanvraag")
    assert qualify_rules(inbound)[0] == Qualification.STRONG


def test_qualifier_agent_updates_stage_and_hands_over(agents):
    db = agents
    good, weak = lead(db, "Goed BV", score=70), lead(db, "Zwak BV", website="https://zwak.example", score=30)
    t = enqueue(db, "lead_qualifier", "qualify_prospects", {"prospect_ids": [good.id, weak.id]}, title="q",
                workflow_id="lead_generation")
    db.commit()
    execute(db, claim_next(db))
    db.refresh(t)
    assert t.status == TaskStatus.COMPLETED
    assert good.stage == LeadStage.QUALIFIED and good.qualification == Qualification.STRONG
    assert weak.stage == LeadStage.NEW and weak.qualification == Qualification.UNQUALIFIED
    child = db.get(AgentTask, t.output["handoff_task_id"])
    assert child.agent_id == "contact_researcher" and child.input["prospect_ids"] == [good.id]


# ---------------------------------------------------------------- contacts


def test_contacts_are_only_taken_from_the_page():
    html = """<html><body><h2>Ons team</h2>
      <p>Jan de Vries, Energiemanager</p><p>Mail: j.devries@staal.nl</p>
      <p>Petra Bakker (inkoper)</p>
      <p>Contact: info@staal.nl · sales@andere-partij.nl</p>
      <a href="https://www.linkedin.com/in/jan-de-vries-123">Jan de Vries</a></body></html>"""
    found = extract_contacts("https://staal.nl/team", html, "staal.nl", "Staal")
    by_name = {f.full_name: f for f in found}
    jan = by_name["Jan de Vries"]
    assert jan.role_category == "ENERGY" and jan.email == "j.devries@staal.nl" and jan.confidence == "HIGH"
    assert jan.linkedin_url == "https://www.linkedin.com/in/jan-de-vries-123"
    assert by_name["Petra Bakker"].email is None and by_name["Petra Bakker"].confidence == "MEDIUM"  # never guessed
    generic = [f for f in found if f.full_name is None]
    assert [g.email for g in generic] == ["info@staal.nl"] and generic[0].email_type == "GENERAL_COMPANY_EMAIL"
    assert all("andere-partij" not in (f.email or "") for f in found)  # other domains are ignored
    assert all(f.source_url == "https://staal.nl/team" and f.excerpt for f in found)


def test_no_contact_found_is_reported_not_invented(agents, monkeypatch):
    import dataclasses

    from app.agents import tools
    from app.integrations.web.contacts import ContactSearchResult

    db = agents
    p = lead(db)
    nothing = ContactSearchResult(pages_read=[p.website], findings=[], errors=[])
    spec = tools.get_tool("web.find_contacts")
    monkeypatch.setitem(tools._TOOLS, "web.find_contacts",
                        dataclasses.replace(spec, handler=lambda website, company_name="": nothing))
    enqueue(db, "contact_researcher", "find_contacts", {"prospect_ids": [p.id], "continue": False}, title="c")
    db.commit()
    execute(db, claim_next(db))
    assert db.scalar(select(func.count()).select_from(Contact)) == 0
    assert "Contact niet gevonden" in p.next_action
    assert db.scalar(select(ProspectEvent.message).where(ProspectEvent.kind == "contact")).startswith(
        "Contact niet gevonden")


def test_contact_researcher_saves_contacts_with_source(agents):
    db = agents
    p = lead(db, website="https://metaal-tilburg-1.example")
    enqueue(db, "contact_researcher", "find_contacts", {"prospect_ids": [p.id], "continue": False}, title="c")
    db.commit()
    execute(db, claim_next(db))
    assert p.contacts and all(c.source_url and c.source_excerpt and c.is_test_data for c in p.contacts)
    assert any(c.email_type == "GENERAL_COMPANY_EMAIL" and c.full_name is None for c in p.contacts)


# ---------------------------------------------------------------- e-mail generation


def test_email_draft_is_short_personal_and_makes_no_promises(db):
    d = outreach_copy.initial_email(company="Staal BV", sector="manufacturing", sector_label="Productie en metaal",
                                    city="Tilburg", multi_location_text="3 vestigingen", contact_name="Jan Jansen",
                                    contact_role="Financieel manager", generic_mailbox=False,
                                    source_url="https://osm.org/1", website="https://staal.nl")
    core = outreach_copy.core_text(d.body)
    assert outreach_copy.word_count(core) <= 150
    assert d.body.startswith("Beste Jan Jansen,") and "Staal BV" in d.body and "Tilburg" in d.body
    assert "3 vestigingen" in d.body  # personal, from a stored fact
    assert "Als we niets terugvinden, betaalt u niets." in d.body  # no cure no pay
    assert "bellen" in d.body  # clear CTA
    assert "afmelden" in d.body  # opt-out
    for banned in ("Ik hoop dat het goed met u gaat", "garantie", "gegarandeerd", "besparing van"):
        assert banned.lower() not in d.body.lower()
    assert {f["source"] for f in d.personalization} >= {"https://osm.org/1", "https://staal.nl"}
    generic = outreach_copy.initial_email(company="X", sector=None, sector_label=None, city=None,
                                          multi_location_text=None, contact_name=None, contact_role=None,
                                          generic_mailbox=True)
    assert generic.body.startswith("Goedendag,") and "doorsturen" in generic.body


def test_outreach_agent_drafts_pending_approval(agents):
    db = agents
    p = lead(db)
    contact(db, p)
    enqueue(db, "outreach", "draft_outreach", {"prospect_ids": [p.id]}, title="o")
    db.commit()
    execute(db, claim_next(db))
    msg = db.scalar(select(OutreachMessage))
    assert msg.status == OutreachStatus.PENDING_APPROVAL and msg.to_email == "j.jansen@staal.example"
    enqueue(db, "outreach", "draft_outreach", {"prospect_ids": [p.id]}, title="o2")
    db.commit()
    execute(db, claim_next(db))
    assert db.scalar(select(func.count()).select_from(OutreachMessage)) == 1  # never two first e-mails


# ---------------------------------------------------------------- approval & sending


def test_nothing_is_sent_without_approval(agents, staff):
    db = agents
    p = lead(db)
    msg = pending_message(db, p, contact(db, p))
    db.commit()
    assert claim_next(db) is None  # a draft creates no send task
    with pytest.raises(crm.CrmError):
        crm.reject_message(db, msg, staff, "  ")  # a rejection needs a reason
    task = crm.approve_and_send(db, msg, staff)
    db.commit()
    assert msg.status == OutreachStatus.APPROVED and msg.decided_by_id == staff.id
    approval = db.scalar(select(AgentApproval).where(AgentApproval.task_id == task.id))
    assert approval.status.value == "APPROVED" and approval.decided_by_id == staff.id
    with pytest.raises(crm.CrmError):
        crm.approve_and_send(db, msg, staff)  # no double approval


def test_rejected_message_is_not_sent(agents, staff):
    db = agents
    p = lead(db)
    msg = pending_message(db, p, contact(db, p))
    crm.reject_message(db, msg, staff, "Verkeerde persoon")
    db.commit()
    assert msg.status == OutreachStatus.REJECTED and claim_next(db) is None


def test_mock_send_is_labelled_and_updates_the_lead(agents, staff, tmp_path):
    db = agents
    p = lead(db)
    msg = pending_message(db, p, contact(db, p))
    crm.approve_and_send(db, msg, staff)
    db.commit()
    run_all(db)
    assert msg.status == OutreachStatus.SENT and msg.delivery == "mock" and msg.delivery_ref.startswith("<")
    assert p.stage == LeadStage.CONTACTED and p.last_contact_at is not None
    files = list((MockEmailProvider().root / "sent").glob("*.eml"))
    assert len(files) == 1 and "X-Factuurspoor-Mock" in files[0].read_text()


def test_live_provider_sends_via_smtp_and_reports_failures(agents, staff, monkeypatch):
    db = agents
    monkeypatch.setenv("ER_EMAIL_PROVIDER", "smtp")
    monkeypatch.setenv("ER_SMTP_HOST", "smtp.example.test")
    from app.config import get_settings

    get_settings.cache_clear()
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            pass

        def login(self, u, p):
            pass

        def send_message(self, m):
            sent.append(m)
            return {}

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    p = lead(db)
    msg = pending_message(db, p, contact(db, p))
    crm.approve_and_send(db, msg, staff)
    db.commit()
    run_all(db)
    assert msg.status == OutreachStatus.SENT and msg.delivery == "live" and len(sent) == 1
    assert sent[0]["To"].endswith("<j.jansen@staal.example>") and sent[0]["List-Unsubscribe"]

    class Down(FakeSMTP):
        def send_message(self, m):
            raise smtplib.SMTPServerDisconnected("gone")

    monkeypatch.setattr(smtplib, "SMTP", Down)
    p2 = lead(db, "Ander BV", website="https://ander.example")
    msg2 = pending_message(db, p2, contact(db, p2, email="a@ander.example", name="Anna Ander"))
    crm.approve_and_send(db, msg2, staff)
    db.commit()
    run_all(db)
    assert msg2.status == OutreachStatus.FAILED and "niet bereikbaar" in msg2.error  # the reason is shown
    assert p2.stage == LeadStage.NEW


def test_live_provider_without_configuration_says_so(monkeypatch):
    monkeypatch.setenv("ER_EMAIL_PROVIDER", "smtp")
    from app.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(EmailNotConfigured, match="ER_SMTP_HOST"):
        SmtpImapProvider().send_email(to_email="a@b.nl", to_name=None, subject="x", body="y")
    with pytest.raises(EmailError, match="ER_IMAP_HOST"):
        SmtpImapProvider().get_inbox()


# ---------------------------------------------------------------- replies


@pytest.mark.parametrize("text,category", [
    ("Interessant. Kunnen we hier volgende week even over bellen?", ReplyCategory.MEETING_REQUEST),
    ("Klinkt goed, ik heb interesse.", ReplyCategory.INTERESTED),
    ("Kunt u meer informatie sturen over de voorwaarden?", ReplyCategory.MORE_INFORMATION),
    ("Geen interesse, dank u.", ReplyCategory.NOT_INTERESTED),
    ("Ik ga hier niet over, dat is mijn collega van inkoop.", ReplyCategory.WRONG_PERSON),
    ("Neem na de zomer maar contact op.", ReplyCategory.FOLLOW_UP),
    ("Graag afmelden, geen mails meer.", ReplyCategory.UNSUBSCRIBE),
    ("Automatisch antwoord: ik ben afwezig tot maandag.", ReplyCategory.OUT_OF_OFFICE),
    ("Ok.", ReplyCategory.OTHER),
])
def test_reply_classification(text, category):
    result = classify_reply("Re: Controle", text)
    assert result.category == category and result.reasons


def test_quoted_original_is_not_classified():
    body = "Nee, dank u.\n\nOp 1 mei schreef Team Factuurspoor:\n> Zullen we volgende week bellen? interesse?"
    assert classify_reply("Re: x", body).category == ReplyCategory.NOT_INTERESTED


def test_unsubscribe_suppresses_address_everywhere(agents, staff):
    db = agents
    p = lead(db)
    c = contact(db, p)
    other = lead(db, "Zuster BV", website="https://zuster.example")
    c2 = contact(db, other, email="j.jansen@staal.example")  # same person listed at a sister company
    pending = pending_message(db, other, c2)
    inbound = crm.register_inbound(db, from_email="j.jansen@staal.example", from_name="Jan", subject="Re: x",
                                   body="Afmelden aub", source="manual")
    crm.apply_classification(db, inbound, classify_reply(inbound.subject, inbound.body), agent_id="email",
                             task_id=None)
    db.commit()
    assert db.scalar(select(Suppression.email)) == "j.jansen@staal.example"
    assert c.do_not_contact and c2.do_not_contact and p.stage == LeadStage.REJECTED
    assert pending.status == OutreachStatus.REJECTED  # open drafts are withdrawn
    with pytest.raises(crm.CrmError, match="suppressielijst|niet meer benaderd"):
        pending_message(db, other, c2)


def test_meeting_request_moves_lead_and_creates_follow_up_task(agents):
    db = agents
    p = lead(db)
    contact(db, p)
    inbound = crm.register_inbound(db, from_email="j.jansen@staal.example", from_name="Jan", subject="Re: x",
                                   body="Interessant. Kunnen we hier volgende week even over bellen?",
                                   source="manual")
    t = enqueue(db, "email", "classify_reply", {"inbound_id": inbound.id}, title="c")
    db.commit()
    execute(db, claim_next(db))
    db.refresh(t)
    assert inbound.category == ReplyCategory.MEETING_REQUEST and p.stage == LeadStage.MEETING
    task = db.scalar(select(LeadTask))
    assert task.kind == "meeting" and task.is_open and "Gesprek inplannen" in task.title
    reply = db.scalar(select(OutreachMessage).where(OutreachMessage.kind == OutreachKind.REPLY))
    assert reply.status == OutreachStatus.PENDING_APPROVAL and reply.in_reply_to_id == inbound.id
    assert p.next_action.startswith("Afspraak bevestigen")


def test_mock_inbox_round_trip_parses_folded_subject(tmp_path):
    mock = MockEmailProvider()
    long_subject = "Re: Controle van de energiefacturen van een bedrijf met een heel lange naam B.V. te Tilburg"
    mock.simulate_incoming(from_email="Jan@Staal.example", from_name="Jan", subject=long_subject, body="Hoi",
                           in_reply_to="<abc@x>")
    [mail] = mock.get_inbox()
    assert mail.subject == long_subject and mail.from_email == "jan@staal.example" and mail.in_reply_to == "<abc@x>"
    assert parse_message(b"From: a@b.nl\nSubject: =?utf-8?q?Caf=C3=A9?=\n\nx").subject == "Café"


# ---------------------------------------------------------------- end to end


def test_end_to_end_lead_to_classified_reply(agents, staff):
    """Research → qualify → contacts → draft → approval → send → reply → classification → lead update."""
    db = agents
    research(db, target=4)
    run_all(db)  # researcher → qualifier → contact researcher → outreach, then stops at the human step
    tasks = {t.agent_id: t for t in db.scalars(select(AgentTask))}
    assert {"lead_researcher", "lead_qualifier", "contact_researcher", "outreach"} <= tasks.keys()
    assert all(t.status == TaskStatus.COMPLETED for t in tasks.values()), [t.error for t in tasks.values()]
    assert "email" not in tasks  # nothing is sent automatically
    drafts = db.scalars(select(OutreachMessage)).all()
    assert drafts and all(m.status == OutreachStatus.PENDING_APPROVAL for m in drafts)

    msg = drafts[0]
    p = db.get(Prospect, msg.prospect_id)
    assert p.stage == LeadStage.QUALIFIED and p.contacts
    crm.approve_and_send(db, msg, staff)
    db.commit()
    run_all(db)
    assert msg.status == OutreachStatus.SENT and p.stage == LeadStage.CONTACTED

    MockEmailProvider().simulate_incoming(from_email=msg.to_email, from_name=msg.to_name, subject=f"Re: {msg.subject}",
                                          body="Interessant. Kunnen we hier volgende week even over bellen?",
                                          in_reply_to=msg.delivery_ref)
    enqueue(db, "email", "fetch_inbox", {}, title="inbox")
    db.commit()
    run_all(db)
    inbound = db.scalar(select(InboundMessage))
    assert inbound.prospect_id == p.id and inbound.outreach_id == msg.id  # linked through the thread
    assert inbound.category == ReplyCategory.MEETING_REQUEST
    assert p.stage == LeadStage.MEETING
    assert db.scalar(select(LeadTask.kind)) == "meeting"
    kinds = [e.kind for e in db.scalars(select(ProspectEvent).where(ProspectEvent.prospect_id == p.id)
                                        .order_by(ProspectEvent.created_at))]
    for expected in ("research", "qualified", "contact", "outreach", "reply", "stage", "task"):
        assert expected in kinds  # every step is traceable on the lead's timeline

    enqueue(db, "email", "fetch_inbox", {}, title="inbox again")
    db.commit()
    run_all(db)
    assert db.scalar(select(func.count()).select_from(InboundMessage)) == 1  # the same mail is not read twice
