"""Command center: topology matches the code, the live event layer sees real runs, the demo stays a demo."""

from decimal import Decimal

from sqlalchemy import func, select

from app.agents.catalog import AGENTS
from app.agents.queue import enqueue
from app.agents.topology import EDGES, LANES
from app.config import get_settings
from app.domain.enums import Role
from app.models import AgentLog, AgentTask, Anomaly
from app.services import observability, simulation
from app.workflows.catalog import WORKFLOWS
from tests.test_operations_agents import run_all, upload_invoice, world  # noqa: F401
from tests.test_web import app_client, login, make_user  # noqa: F401

AGENT_IDS = {a.id for a in AGENTS}


def test_topology_uses_only_real_agents_and_covers_every_workflow_step():
    assert {e.source for e in EDGES} | {e.target for e in EDGES} <= AGENT_IDS
    placed = [a for _, _, ids in LANES for a in ids]
    assert sorted(placed) == sorted(AGENT_IDS)  # all 16, each exactly once
    pairs = {(e.source, e.target) for e in EDGES}
    for wf in WORKFLOWS.values():
        for a, b in zip(wf.stages, wf.stages[1:], strict=False):
            assert (a.agent_id, b.agent_id) in pairs, f"{wf.id}: {a.agent_id} → {b.agent_id} missing"


def test_demo_scenario_is_consistent_and_labelled():
    s = simulation.scenario()
    events = s["events"]
    assert s["mode"] == "demo" and all(e["mode"] == "demo" for e in events)
    assert [e["at"] for e in events] == sorted(e["at"] for e in events)
    assert events[-1]["at"] <= s["duration_ms"]
    pairs = {(e.source, e.target) for e in EDGES}
    for e in events:
        assert e["agent"] in AGENT_IDS
        if e["kind"] == "handoff":
            assert (e["agent"], e["to"]) in pairs, e  # packets only travel along real connections
    # The fictitious numbers add up.
    assert (Decimal("312450") + Decimal("1578620")) * (Decimal("0.1248") - Decimal("0.1182")) == Decimal("12481.0620")
    assert Decimal("312450") * Decimal("0.1248") + Decimal("9297.56") == Decimal("48291.32")
    final = [e for e in events if e["data"].get("case")][-1]["data"]["case"]
    assert all(st["state"] == "done" for st in final["stages"]) and final["opportunity"]["validated"] == 12481.06
    assert "fictief" in final["invoice"]["supplier"] and final["synthetic"]


def test_live_events_follow_a_real_recovery_run(world):  # noqa: F811
    db, staff, client = world
    upload_invoice(db, client, staff)
    enqueue(db, "orchestrator", "plan_workflow", {"goal": "recover_client", "client_id": client.id},
            title="Terugvordering", client_id=client.id)
    db.commit()
    run_all(db)
    events = observability.recent_events(db)
    assert events and all(e["mode"] == "live" for e in events)
    handoffs = [(e["agent"], e["to"]) for e in events if e["kind"] == "handoff"]
    assert ("orchestrator", "invoice_intake") in handoffs and ("invoice_intake", "invoice_analysis") in handoffs
    assert ("invoice_analysis", "audit") in handoffs
    assert len({e["id"] for e in events}) == len(events)  # stable, unique ids (the browser de-duplicates on them)
    kinds = {e["kind"] for e in events}
    assert {"task.started", "task.completed"} <= kinds
    case = observability.live_case(db)
    assert case["client"] == client.company_name
    states = {s["key"]: s["state"] for s in case["stages"]}
    assert states["received"] == states["extracted"] == states["analyzed"] == states["audited"] == "done"
    assert {a["agent"]: a["state"] for a in case["agents"]}["audit"] == "done"
    if db.scalar(select(func.count()).select_from(Anomaly)):
        assert case["evidence"]["sources"] >= 1 and case["opportunity"]["potential"] > 0
    snap = observability.snapshot(db)
    assert snap["mode"] == "live" and len(snap["agents"]) == 16
    assert snap["agents"]["audit"]["recent"]  # the side panel's recent activity comes from real logs


def test_demo_writes_nothing(world):  # noqa: F811
    db, _, _ = world
    before = (db.scalar(select(func.count()).select_from(AgentTask)), db.scalar(select(func.count()).select_from(AgentLog)))
    simulation.scenario()
    after = (db.scalar(select(func.count()).select_from(AgentTask)), db.scalar(select(func.count()).select_from(AgentLog)))
    assert before == after


def test_command_center_api_and_demo_gate(app_client):  # noqa: F811
    make_user("ops@fs.nl", Role.REVIEWER)
    login(app_client, "ops@fs.nl")
    page = app_client.get("/app/ops").text
    assert 'id="ops-boot"' in page and "data-demo-play" in page and "Agentnetwerk" in page
    live = app_client.get("/app/ops/api/live").json()
    assert live["snapshot"]["mode"] == "live" and set(live["snapshot"]["metrics"]) == {
        "invoices", "analyses", "potential", "validated", "cases_open", "cases_recovered"}
    assert app_client.get("/app/ops/api/demo").json()["mode"] == "demo"
    settings = get_settings()
    original = settings.environment
    try:
        settings.environment = "production"  # DEMO MODE is never offered in production
        assert app_client.get("/app/ops/api/demo").status_code == 404
        assert "data-demo-play" not in app_client.get("/app/ops").text
    finally:
        settings.environment = original
