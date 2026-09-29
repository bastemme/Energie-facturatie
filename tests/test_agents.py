"""Agent infrastructure: registry, queue, execution, permissions, approvals, handoffs, context, logs."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.agents import registry
from app.agents.approvals import ApprovalError, decide
from app.agents.base import Agent, AgentError
from app.agents.lead_researcher import SECTORS, LeadResearcher, score_candidate
from app.agents.queue import cancel, claim_next, enqueue, retry
from app.agents.runner import execute
from app.domain.enums import AgentStatus, ApprovalStatus, Role, TaskStatus
from app.integrations.web.http import UnsafeURLError, WebAccessError, _check_url
from app.integrations.web.openstreetmap import build_query, parse_elements
from app.integrations.web.research import BusinessCandidate
from app.integrations.web.website import analyse_html, domain_of
from app.models import AgentApproval, AgentLog, AgentMessage, AgentRecord, AgentTask, Prospect, SharedContext, User
from app.models.base import utcnow


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


def use_impls(monkeypatch, db, impls):
    monkeypatch.setattr(registry, "_implementations", lambda: impls)
    registry.sync_agents(db)


def solo(db, agent_id="lead_researcher"):
    """Only `agent_id` takes work, so a test can look at one stage without the next stages running."""
    for rec in db.scalars(select(AgentRecord)):
        rec.enabled = rec.id == agent_id
    db.commit()


def run_one(db):
    task = claim_next(db)
    assert task is not None, "no runnable task"
    execute(db, task)
    db.refresh(task)
    return task


def research(db, **kw):
    payload = {"sectors": ["bakeries", "hotels"], "area": "Tilburg", "limit": 8, "require_website": True} | kw
    t = enqueue(db, "lead_researcher", "research_prospects", payload, title="test", workflow_id="lead_generation")
    db.commit()
    return t


def events(db, task):
    return [row.event for row in db.scalars(select(AgentLog).where(AgentLog.task_id == task.id)
                                             .order_by(AgentLog.created_at, AgentLog.id))]


# ---------------------------------------------------------------- registry & queue


def test_sync_agents_registers_all_16_and_keeps_runtime_state(agents):
    db = agents
    rows = db.scalars(select(AgentRecord)).all()
    assert len(rows) == 16
    assert {r.id for r in rows if r.implemented} == {"lead_researcher", "lead_qualifier", "contact_researcher",
                                                     "outreach", "email", "follow_up"}
    rec = db.get(AgentRecord, "email")
    rec.enabled = False
    db.commit()
    registry.sync_agents(db)
    assert db.get(AgentRecord, "email").enabled is False
    assert set(db.get(AgentRecord, "lead_researcher").tools) >= {"web.search_businesses", "prospects.save"}


def test_enqueue_validates_agent_and_task_type(agents):
    with pytest.raises(ValueError):
        enqueue(agents, "nobody", "x", {}, title="t")
    with pytest.raises(ValueError):
        enqueue(agents, "lead_researcher", "send_email", {}, title="t")


def test_claim_respects_priority_readiness_and_implementation(agents):
    db = agents
    enqueue(db, "invoice_intake", "intake_documents", {}, title="not built", priority=9)
    low = enqueue(db, "lead_researcher", "research_prospects", {}, title="low", priority=2)
    later = enqueue(db, "lead_researcher", "research_prospects", {}, title="later", priority=9)
    later.not_before = utcnow() + timedelta(minutes=5)
    high = enqueue(db, "lead_researcher", "research_prospects", {}, title="high", priority=7)
    db.commit()
    first = claim_next(db)
    assert first.id == high.id and first.status == TaskStatus.RUNNING and first.attempts == 1
    assert claim_next(db).id == low.id
    assert claim_next(db) is None  # 'later' not due, qualifier not implemented
    db.get(AgentRecord, "lead_researcher").enabled = False
    later.not_before = None
    db.commit()
    assert claim_next(db) is None  # disabled agents take no work


def test_cancel_and_retry_rules(agents):
    db = agents
    t = enqueue(db, "lead_researcher", "research_prospects", {}, title="t")
    with pytest.raises(ValueError):
        retry(db, t)
    cancel(db, t, "stop")
    assert t.status == TaskStatus.CANCELLED
    with pytest.raises(ValueError):
        cancel(db, t, "again")
    retry(db, t)
    assert t.status == TaskStatus.QUEUED and t.error is None


# ---------------------------------------------------------------- Lead Researcher end to end


def test_lead_researcher_end_to_end_with_test_provider(agents):
    db = agents
    task = research(db)
    run_one(db)
    db.refresh(task)
    assert task.status == TaskStatus.COMPLETED, task.error
    out = task.output
    assert out["provider"] == "mock" and out["test_data"] is True and "TESTDATA" in out["summary"]
    prospects = db.scalars(select(Prospect).where(Prospect.research_task_id == task.id)).all()
    assert len(prospects) == out["saved"] > 0
    assert all(p.is_test_data and p.website and p.evidence and p.fit_reasons for p in prospects)
    assert all(p.domain.endswith(".example") for p in prospects)
    assert out["skipped_no_website"] > 0  # require_website honoured
    # qualified prospects are handed to the next workflow stage
    child = db.get(AgentTask, out["handoff_task_id"])
    assert child.agent_id == "lead_qualifier" and child.status == TaskStatus.QUEUED
    assert child.parent_task_id == task.id and child.workflow_run_id == task.workflow_run_id
    assert len(child.input["prospect_ids"]) == out["qualified"]
    assert all(db.get(Prospect, pid).fit_score >= out["threshold"] for pid in child.input["prospect_ids"])
    msg = db.scalar(select(AgentMessage).where(AgentMessage.source_task_id == task.id))
    assert msg.kind == "HANDOFF" and msg.to_agent_id == "lead_qualifier" and msg.created_task_id == child.id
    # shared context and logs
    shared = db.scalar(select(SharedContext).where(SharedContext.key == "prospect_ids"))
    assert shared.scope == f"workflow:{task.workflow_run_id}" and len(shared.value) == out["saved"]
    ev = events(db, task)
    assert ev[0] == "task.started" and ev[-1] == "task.completed"
    assert {"plan", "tool.call", "handoff", "context.write"} <= set(ev)
    calls = db.scalars(select(AgentLog).where(AgentLog.task_id == task.id, AgentLog.event == "tool.call")).all()
    assert all(c.duration_ms is not None and c.data["tool"] for c in calls)
    rec = db.get(AgentRecord, "lead_researcher")
    assert rec.status == AgentStatus.IDLE and rec.last_run_at is not None and rec.current_task_id is None


def test_lead_researcher_skips_known_companies_on_rerun(agents):
    db = agents
    solo(db)
    first = research(db)
    run_one(db)
    db.refresh(first)
    before = db.scalar(select(func.count()).select_from(Prospect))
    second = research(db)
    run_one(db)
    db.refresh(second)
    assert second.status == TaskStatus.COMPLETED
    assert second.output["saved"] == 0 and second.output["duplicates"] == first.output["saved"]
    assert second.output["handoff_task_id"] is None
    assert db.scalar(select(func.count()).select_from(Prospect)) == before


def test_lead_researcher_rejects_invalid_input(agents):
    db = agents
    task = research(db, sectors=["unknown"])
    run_one(db)
    assert task.status == TaskStatus.FAILED and "branche" in task.error
    assert db.get(AgentRecord, "lead_researcher").status == AgentStatus.FAILED


def test_score_is_explained_component_by_component():
    s = SECTORS["supermarkets"]
    c = BusinessCandidate(name="X", sector="supermarkets", source="t", source_ref="t:1", source_url="https://x",
                          street="Markt 1", city="Breda", website="https://x.nl", brand="Keten")
    score, reasons = score_candidate(s, c, None)
    assert score == 35 + 10 + 5 - 15
    assert len(reasons) == 4 and reasons[-1].startswith("−15")


# ---------------------------------------------------------------- approvals


def test_large_research_waits_for_approval_then_resumes(agents, staff):
    db = agents
    solo(db)
    task = research(db, limit=40)
    run_one(db)
    assert task.status == TaskStatus.WAITING_APPROVAL
    assert db.scalar(select(func.count()).select_from(Prospect)) == 0  # nothing done before approval
    assert db.get(AgentRecord, "lead_researcher").status == AgentStatus.WAITING
    approval = db.scalar(select(AgentApproval).where(AgentApproval.task_id == task.id))
    assert approval.action == "large_research" and approval.status == ApprovalStatus.PENDING
    decide(db, approval, staff, approve=True, note=None)
    db.commit()
    assert task.status == TaskStatus.QUEUED
    with pytest.raises(ApprovalError):
        decide(db, approval, staff, approve=False, note="te laat")
    run_one(db)
    assert task.status == TaskStatus.COMPLETED and task.attempts == 2
    assert "approval.granted" in events(db, task)


def test_rejected_approval_cancels_task(agents, staff):
    db = agents
    solo(db)
    task = research(db, limit=40)
    run_one(db)
    approval = db.scalar(select(AgentApproval).where(AgentApproval.task_id == task.id))
    with pytest.raises(ApprovalError):
        decide(db, approval, staff, approve=False, note="  ")
    decide(db, approval, staff, approve=False, note="Te breed")
    db.commit()
    assert task.status == TaskStatus.CANCELLED and "Te breed" in task.error
    assert claim_next(db) is None


def test_sensitive_actions_always_need_approval(monkeypatch, agents):
    db = agents

    class Mailer(Agent):
        spec = registry.get_spec("email")

        def run(self, ctx):
            ctx.guard_sensitive("email.send", "Mail naar prospect")
            return {"summary": "verstuurd"}

    use_impls(monkeypatch, db, {"email": Mailer()})
    task = enqueue(db, "email", "send_email", {}, title="mail")
    db.commit()
    run_one(db)
    assert task.status == TaskStatus.WAITING_APPROVAL
    assert db.scalar(select(AgentApproval).where(AgentApproval.task_id == task.id)).action == "email.send"


# ---------------------------------------------------------------- permissions, failures, retries


def test_tools_outside_the_agent_toolset_are_denied_and_logged(monkeypatch, agents):
    db = agents

    class Rogue(Agent):
        spec = registry.get_spec("lead_researcher")

        def run(self, ctx):
            ctx.use("email.send", to="someone@example.com")
            return {}

    use_impls(monkeypatch, db, {"lead_researcher": Rogue()})
    task = research(db)
    run_one(db)
    assert task.status == TaskStatus.FAILED
    assert "permission.denied" in events(db, task)


def test_failure_rolls_back_agent_writes_but_keeps_logs(monkeypatch, agents):
    db = agents

    class HalfWay(Agent):
        spec = registry.get_spec("lead_researcher")

        def run(self, ctx):
            ctx.use("prospects.save", company_name="Halve B.V.", sector="bakeries", source="test",
                    source_ref="t:1", evidence=[], fit_score=1, fit_reasons=[], is_test_data=True)
            raise AgentError("stopt halverwege")

    use_impls(monkeypatch, db, {"lead_researcher": HalfWay()})
    task = research(db)
    run_one(db)
    assert task.status == TaskStatus.FAILED and task.error == "stopt halverwege"
    assert db.scalar(select(func.count()).select_from(Prospect)) == 0
    assert "tool.call" in events(db, task) and events(db, task)[-1] == "task.failed"
    assert db.get(AgentRecord, "lead_researcher").last_error == "stopt halverwege"


def test_temporary_web_errors_are_retried_with_backoff(monkeypatch, agents):
    db = agents

    class Offline(Agent):
        spec = registry.get_spec("lead_researcher")

        def run(self, ctx):
            raise WebAccessError("Overpass niet bereikbaar")

    use_impls(monkeypatch, db, {"lead_researcher": Offline()})
    task = research(db)
    run_one(db)
    assert task.status == TaskStatus.QUEUED and task.not_before is not None
    assert "task.retry_scheduled" in events(db, task)
    task.not_before = None
    db.commit()
    run_one(db)
    assert task.status == TaskStatus.FAILED and task.attempts == 2


def test_unexpected_errors_are_scrubbed(monkeypatch, agents):
    db = agents

    class Buggy(Agent):
        spec = registry.get_spec("lead_researcher")

        def run(self, ctx):
            raise RuntimeError("boom for jan@voorbeeld.nl")

    use_impls(monkeypatch, db, {"lead_researcher": Buggy()})
    task = research(db)
    run_one(db)
    assert task.status == TaskStatus.FAILED and "RuntimeError" in task.error
    assert "jan@voorbeeld.nl" not in task.error


# ---------------------------------------------------------------- integrations


def test_ssrf_guard_blocks_non_public_targets():
    for url in ("file:///etc/passwd", "ftp://example.org", "http://127.0.0.1/", "http://10.0.0.5/",
                "http://169.254.169.254/latest/meta-data", "http://[::1]/"):
        with pytest.raises(UnsafeURLError):
            _check_url(url)


def test_overpass_query_and_parsing():
    q = build_query(['["shop"="bakery"]'], 'Til"burg', 5)
    assert 'area["name"="Tilburg"]' in q and 'nwr["shop"="bakery"]["name"](area.a);' in q and "out center tags 5" in q
    payload = {"elements": [
        {"type": "node", "id": 1, "lat": 51.56, "lon": 5.08,
         "tags": {"name": "Bakkerij Jansen", "addr:street": "Heuvel", "addr:housenumber": "3",
                  "addr:postcode": "5038 AA", "addr:city": "Tilburg", "website": "www.jansen.nl"}},
        {"type": "way", "id": 2, "center": {"lat": 51.5, "lon": 5.0}, "tags": {"name": "Keten", "brand": "K"}},
        {"type": "node", "id": 3, "lat": 48.8, "lon": 2.3, "tags": {"name": "Paris"}},  # outside NL
        {"type": "node", "id": 4, "lat": 51.5, "lon": 5.0, "tags": {}},  # no name
    ]}
    out = parse_elements(payload, "bakeries")
    assert [c.name for c in out] == ["Bakkerij Jansen", "Keten"]
    first = out[0]
    assert first.website == "https://www.jansen.nl" and first.street == "Heuvel 3" and first.source_ref == "osm:node/1"
    assert first.source_url == "https://www.openstreetmap.org/node/1" and not first.is_test_data
    assert out[1].brand == "K"
    assert domain_of(first.website) == "jansen.nl"


def test_website_analysis_reads_public_company_facts():
    body = ("<html><head><title> Bakkerij &amp; Co </title><meta name='description' content='Vers brood'></head>"
            "<body>Wij hebben 4 vestigingen in Brabant. KvK: 12345678</body></html>")
    facts = analyse_html("https://b.nl", "https://b.nl/", 200, body)
    assert facts.title == "Bakkerij & Co" and facts.description == "Vers brood"
    assert facts.kvk_number == "12345678" and "4 vestigingen" in facts.multi_location


def test_research_provider_is_mock_in_tests():
    assert LeadResearcher.spec.id == "lead_researcher"
    from app.integrations.web.research import get_research_provider

    assert get_research_provider().is_test_data is True


def _fake_overpass(monkeypatch, responder):
    import json as _json

    from app.integrations.web import http as web_http

    calls = []

    def post_form(url, data, **kw):
        calls.append((url, data["data"]))
        status, payload = responder(url, data["data"], len(calls))
        return web_http.HttpResponse(url, status, "application/json", _json.dumps(payload), False)

    monkeypatch.setattr(web_http, "post_form", post_form)
    return calls


def _element(i):
    return {"type": "node", "id": i, "lat": 51.5, "lon": 5.0, "tags": {"name": f"Bedrijf {i}", "website": "x.nl"}}


def test_busy_overpass_server_falls_back_to_the_next(monkeypatch):
    from app.integrations.web.openstreetmap import OpenStreetMapProvider

    calls = _fake_overpass(monkeypatch, lambda url, q, n: (504, {}) if "overpass-api.de" in url
                           else (200, {"elements": [_element(1)]}))
    out = OpenStreetMapProvider().search_businesses(['["man_made"="works"]'], "Tilburg", 5, "manufacturing")
    assert [c.name for c in out] == ["Bedrijf 1"]
    assert "overpass-api.de" in calls[0][0] and "kumi" in calls[1][0]


def test_all_servers_busy_gives_a_clear_error(monkeypatch):
    from app.integrations.web.openstreetmap import OpenStreetMapProvider

    _fake_overpass(monkeypatch, lambda url, q, n: (504, {}))
    with pytest.raises(WebAccessError, match="overbelast"):
        OpenStreetMapProvider().search_businesses(['["man_made"="works"]'], "Tilburg", 5, "manufacturing")


def test_whole_country_is_searched_province_by_province(monkeypatch):
    from app.integrations.web.openstreetmap import OpenStreetMapProvider

    def responder(url, q, n):
        if "NL-" not in q:
            return 500, {}
        return 200, {"elements": [_element(n * 10 + k) for k in range(2)]}

    calls = _fake_overpass(monkeypatch, responder)
    out = OpenStreetMapProvider().search_businesses(['["man_made"="works"]'], "Nederland", 5, "manufacturing")
    assert len(out) == 5 and len(calls) == 3  # stops once enough companies are found
    assert all('"ISO3166-2"="NL-' in q for _, q in calls)
