"""The lead-generation workflow through the web UI (routes, forms, CSRF, background runs)."""

from sqlalchemy import select

from app.domain.enums import LeadStage, OutreachStatus, ReplyCategory, Role, TaskStatus
from app.models import AgentTask, InboundMessage, OutreachMessage, Prospect, Suppression
from tests.test_web import app_client, csrf, login, make_client, make_user, session  # noqa: F401

PAGES = ["/app/dashboard", "/app/leads", "/app/leads/find", "/app/companies", "/app/companies?tab=clients",
         "/app/contacts", "/app/outreach", "/app/inbox", "/app/ops", "/app/ops/tasks", "/app/settings"]


def test_workflow_pages_are_staff_only(app_client):  # noqa: F811
    cid = make_client()
    make_user("klant@k.nl", Role.CLIENT, client_id=cid)
    login(app_client, "klant@k.nl")
    for url in PAGES:
        assert app_client.get(url, follow_redirects=False).status_code == 403, url


def test_empty_pages_render(app_client):  # noqa: F811
    make_user("ops@fs.nl", Role.REVIEWER)
    login(app_client, "ops@fs.nl")
    for url in PAGES:
        r = app_client.get(url)
        assert r.status_code == 200, url
    assert "Nog geen leads" in app_client.get("/app/leads").text
    assert "Mock" in app_client.get("/app/outreach").text  # MOCK is visible


def test_full_workflow_through_the_ui(app_client):  # noqa: F811
    make_user("ops@fs.nl", Role.ADMIN)
    login(app_client, "ops@fs.nl")
    token = csrf(app_client, "/app/leads/find")
    r = app_client.post("/app/leads/find", data={"csrf_token": token, "preset": "energy_intensive", "country": "NL",
                                                 "target": "5", "min_locations": "1", "require_website": "on",
                                                 "workflow": "on"}, follow_redirects=False)
    assert r.status_code == 303
    with session() as s:
        tasks = s.scalars(select(AgentTask)).all()
        assert {t.agent_id for t in tasks} == {"lead_researcher", "lead_qualifier", "contact_researcher", "outreach"}
        assert all(t.status == TaskStatus.COMPLETED for t in tasks)
        msg = s.scalars(select(OutreachMessage)).first()
        mid, pid = msg.id, msg.prospect_id
    ops = app_client.get("/app/ops").text
    assert "Live activiteit" in ops and "Lead Researcher" in ops
    page = app_client.get(f"/app/outreach/{mid}").text
    for part in ("Goedkeuren en versturen", "Bewerken", "Afwijzen", "Waarom dit bedrijf", "Onderzoek en bronnen"):
        assert part in page
    assert "Contacten" in app_client.get(f"/app/leads/{pid}").text

    # edit, then approve
    r = app_client.post(f"/app/outreach/{mid}/save", data={"csrf_token": token, "subject": "Nieuw onderwerp",
                                                           "body": "Beste,\n\nKorte tekst."})
    assert r.status_code == 200
    app_client.post(f"/app/outreach/{mid}/approve", data={"csrf_token": token})
    with session() as s:
        m = s.get(OutreachMessage, mid)
        assert m.status == OutreachStatus.SENT and m.delivery == "mock" and m.subject == "Nieuw onderwerp"
        assert s.get(Prospect, pid).stage == LeadStage.CONTACTED

    # a reply comes in (MOCK), gets classified, the lead moves on
    app_client.post("/app/inbox/simulate", data={"csrf_token": token, "message_id": mid,
                                                 "body": "Interessant. Kunnen we hier volgende week even over bellen?"})
    with session() as s:
        inbound = s.scalars(select(InboundMessage)).one()
        assert inbound.category == ReplyCategory.MEETING_REQUEST
        assert s.get(Prospect, pid).stage == LeadStage.MEETING
        iid = inbound.id
    assert "Wil een gesprek" in app_client.get("/app/inbox?show=all").text
    assert "Antwoord beoordelen" in app_client.get(f"/app/inbox/{iid}").text
    assert "Gesprek inplannen" in app_client.get(f"/app/leads/{pid}").text


def test_reject_requires_reason_and_unsubscribe_is_visible(app_client):  # noqa: F811
    make_user("ops@fs.nl", Role.ADMIN)
    login(app_client, "ops@fs.nl")
    token = csrf(app_client, "/app/leads/find")
    app_client.post("/app/leads/find", data={"csrf_token": token, "preset": "energy_intensive", "target": "3",
                                             "require_website": "on", "workflow": "on"})
    with session() as s:
        first, second = s.scalars(select(OutreachMessage)).all()[:2]
        mid, other = first.id, second.id
    app_client.post(f"/app/outreach/{mid}/reject", data={"csrf_token": token, "note": ""})
    with session() as s:
        assert s.get(OutreachMessage, mid).status == OutreachStatus.PENDING_APPROVAL
    app_client.post(f"/app/outreach/{mid}/reject", data={"csrf_token": token, "note": "Past niet"})
    with session() as s:
        assert s.get(OutreachMessage, mid).status == OutreachStatus.REJECTED

    app_client.post(f"/app/outreach/{other}/approve", data={"csrf_token": token})
    app_client.post("/app/inbox/simulate", data={"csrf_token": token, "message_id": other,
                                                 "body": "Graag afmelden, geen mails meer."})
    with session() as s:
        m = s.get(OutreachMessage, other)
        assert s.scalar(select(Suppression.email)) == m.to_email
        pid = m.prospect_id
    assert "Niet benaderen" in app_client.get(f"/app/leads/{pid}").text
    assert "suppressielijst" in app_client.get("/app/settings").text
