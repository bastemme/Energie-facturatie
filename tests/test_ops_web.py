"""AI Operations UI: access control, starting a Lead Research task, approvals, live pulse."""

from sqlalchemy import select

from app.domain.enums import Role, TaskStatus
from app.models import AgentApproval, AgentTask, Prospect
from tests.test_web import app_client, csrf, login, make_client, make_user, session  # noqa: F401


def test_ops_pages_are_staff_only(app_client):  # noqa: F811
    cid = make_client()
    make_user("klant@k.nl", Role.CLIENT, client_id=cid)
    login(app_client, "klant@k.nl")
    for url in ("/app/ops", "/app/ops/research/new", "/app/ops/prospects", "/app/ops/agents/lead_researcher"):
        assert app_client.get(url, follow_redirects=False).status_code == 403


def test_start_lead_research_from_ui_runs_to_completion(app_client):  # noqa: F811
    make_user("ops@fs.nl", Role.REVIEWER)
    login(app_client, "ops@fs.nl")
    r = app_client.get("/app/ops")
    assert r.status_code == 200 and "Lead Researcher" in r.text and "Nog niet gebouwd" in r.text
    form = app_client.get("/app/ops/research/new")
    assert "TESTDATA" in form.text  # the test source is announced before starting
    token = csrf(app_client, "/app/ops/research/new")
    r = app_client.post("/app/ops/research", data={"csrf_token": token, "sectors": ["bakeries", "hotels"],
                                                   "area": "Tilburg", "limit": "8", "require_website": "on"},
                        follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/app/ops/tasks/")
    task_id = r.headers["location"].rsplit("/", 1)[1]
    with session() as s:
        task = s.get(AgentTask, task_id)
        assert task.status == TaskStatus.COMPLETED, task.error  # ran in the background after the response
        assert task.created_by_user_id is not None and task.workflow_id == "lead_generation"
        prospect = s.scalars(select(Prospect).where(Prospect.research_task_id == task_id)).first()
        child = s.scalars(select(AgentTask).where(AgentTask.parent_task_id == task_id)).one()
    page = app_client.get(f"/app/ops/tasks/{task_id}").text
    assert "Gevonden bedrijven" in page and "TESTDATA" in page and "Overdrachten" in page
    assert app_client.get(f"/app/ops/tasks/{child.id}").status_code == 200
    assert prospect.company_name in app_client.get("/app/ops/prospects").text
    assert "Fitscore" in app_client.get(f"/app/ops/prospects/{prospect.id}").text
    assert app_client.get("/app/ops/pulse").json()["v"]


def test_research_form_validation(app_client):  # noqa: F811
    make_user("ops@fs.nl", Role.REVIEWER)
    login(app_client, "ops@fs.nl")
    token = csrf(app_client, "/app/ops/research/new")
    r = app_client.post("/app/ops/research", data={"csrf_token": token, "area": "", "limit": "0"})
    assert "Kies ten minste één sector." in r.text
    with session() as s:
        assert s.scalar(select(AgentTask)) is None


def test_approval_through_ui(app_client):  # noqa: F811
    make_user("ops@fs.nl", Role.REVIEWER)
    login(app_client, "ops@fs.nl")
    token = csrf(app_client, "/app/ops/research/new")
    r = app_client.post("/app/ops/research", data={"csrf_token": token, "sectors": ["bakeries", "hotels"],
                                                   "area": "Utrecht", "limit": "40"}, follow_redirects=False)
    task_id = r.headers["location"].rsplit("/", 1)[1]
    with session() as s:
        assert s.get(AgentTask, task_id).status == TaskStatus.WAITING_APPROVAL
        approval_id = s.scalar(select(AgentApproval.id).where(AgentApproval.task_id == task_id))
    assert "Goedkeuring nodig" in app_client.get("/app/ops").text
    r = app_client.post(f"/app/ops/approvals/{approval_id}",
                        data={"csrf_token": csrf(app_client, "/app/ops"), "decision": "approve",
                              "next_url": f"/app/ops/tasks/{task_id}"})
    assert r.status_code == 200
    with session() as s:
        assert s.get(AgentTask, task_id).status == TaskStatus.COMPLETED


def test_only_admins_toggle_agents(app_client):  # noqa: F811
    make_user("rev@fs.nl", Role.REVIEWER)
    login(app_client, "rev@fs.nl")
    r = app_client.post("/app/ops/agents/lead_researcher/toggle",
                        data={"csrf_token": csrf(app_client, "/app/ops")}, follow_redirects=False)
    assert r.status_code == 403
