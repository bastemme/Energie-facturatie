"""Invoice, recovery and platform agents on top of the existing deterministic services."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.agents import registry
from app.agents.approvals import decide
from app.agents.queue import claim_next, enqueue
from app.agents.runner import execute
from app.devtools.synthetic import render_invoice_pdf
from app.domain.enums import CaseStatus, Classification, Confidence, Role, TaskStatus
from app.ingestion.pipeline import ingest_upload
from app.models import AgentApproval, AgentTask, Anomaly, CaseEvent, Client, OutreachMessage, RecoveryCase, User
from app.models.base import utcnow
from app.services import cases as case_service
from app.services import crm, outreach_copy
from app.services.operations import audit_anomaly
from app.services.review import review
from tests.factories import electricity_invoice


@pytest.fixture
def world(db):
    registry.sync_agents(db)
    staff = User(email="ops@fs.nl", password_hash="x", role=Role.ADMIN)
    client = Client(company_name="Bakkerij Test B.V.", success_fee_percentage=Decimal("20"), consent_given=True,
                    contact_name="Piet", contact_email="piet@bakkerij.example")
    db.add_all([staff, client])
    db.commit()
    return db, staff, client


def run_all(db, limit=40):
    n = 0
    while n < limit and (t := claim_next(db)) is not None:
        execute(db, t)
        n += 1
    return n


def tasks_by_agent(db):
    return {t.agent_id: t for t in db.scalars(select(AgentTask).order_by(AgentTask.number))}


def upload_invoice(db, client, staff, amount="300"):
    pdf = render_invoice_pdf(electricity_invoice(normal_amount=Decimal(amount)))
    doc = ingest_upload(db, client, "factuur.pdf", pdf, staff)
    db.commit()
    return doc


def test_invoice_chain_intake_analysis_audit(world):
    db, staff, client = world
    upload_invoice(db, client, staff)
    enqueue(db, "invoice_intake", "intake_documents", {"client_id": client.id}, title="intake",
            workflow_id="invoice_recovery", client_id=client.id)
    db.commit()
    run_all(db)
    t = tasks_by_agent(db)
    assert t["invoice_intake"].status == TaskStatus.COMPLETED and t["invoice_intake"].output["invoices"] == 1
    assert t["invoice_analysis"].status == TaskStatus.COMPLETED and t["invoice_analysis"].output["findings"] >= 1
    assert t["audit"].status == TaskStatus.COMPLETED
    assert "recovery" not in t  # nothing is confirmed yet: a specialist reviews first
    assert t["audit"].output["awaiting_review"] >= 1


def test_audit_flags_untraceable_findings():
    good = Anomaly(evidence=[{"document_id": "d", "page": 1}], calculation=["a - b"], potential_recovery=Decimal(5),
                   actual_value=Decimal(10), expected_value=Decimal(5), difference=Decimal(5), unit="EUR",
                   classification=Classification.POTENTIAL_ERROR, confidence=Confidence.HIGH, invoice_id="i")
    assert audit_anomaly(good) == []
    bad = Anomaly(evidence=[], calculation=[], potential_recovery=Decimal(5), actual_value=Decimal(10),
                  expected_value=Decimal(5), difference=Decimal(4), unit="EUR",
                  classification=Classification.ANOMALY, confidence=Confidence.HIGH, extraction_confidence=0.5)
    problems = " | ".join(audit_anomaly(bad))
    for expected in ("bronverwijzing", "berekening", "signaal", "verschil klopt niet", "zekerheid", "factuur"):
        assert expected in problems


def _confirmed_finding(db, client, staff):
    upload_invoice(db, client, staff)
    enqueue(db, "invoice_analysis", "analyse_client", {"client_id": client.id}, title="a", client_id=client.id)
    db.commit()
    run_all(db)
    a = db.scalars(select(Anomaly).where(Anomaly.potential_recovery > 0)).first()
    review(db, a, "confirm", staff, note="gecontroleerd")
    db.commit()
    return a


def test_recovery_and_claims_with_approval(world):
    db, staff, client = world
    finding = _confirmed_finding(db, client, staff)
    enqueue(db, "orchestrator", "plan_workflow", {"goal": "daily"}, title="daily")
    db.commit()
    run_all(db)
    case = db.scalar(select(RecoveryCase))
    assert case is not None and finding.case_id == case.id and case.status == CaseStatus.REVIEW
    claim_task = tasks_by_agent(db)["claims"]
    assert claim_task.status == TaskStatus.COMPLETED and "Geachte" in claim_task.output["letter"]
    assert db.scalar(select(CaseEvent).where(CaseEvent.event_type == "CLAIM_DRAFTED")) is not None

    case_service.transition(db, case, CaseStatus.VERIFIED, staff)
    enqueue(db, "claims", "draft_claim", {"case_id": case.id}, title="draft")
    db.commit()
    run_all(db)
    assert case.status == CaseStatus.CLAIM_PREPARED

    submit = enqueue(db, "claims", "submit_claim", {"case_id": case.id}, title="submit")
    db.commit()
    run_all(db)
    assert submit.status == TaskStatus.WAITING_APPROVAL and case.status == CaseStatus.CLAIM_PREPARED
    approval = db.scalar(select(AgentApproval).where(AgentApproval.task_id == submit.id))
    decide(db, approval, staff, approve=True, note=None)
    db.commit()
    run_all(db)
    assert submit.status == TaskStatus.COMPLETED and case.status == CaseStatus.SUBMITTED


def test_customer_update_needs_approval_and_sends_what_was_approved(world):
    db, staff, client = world
    t = enqueue(db, "customer_success", "client_update", {"client_id": client.id}, title="u")
    db.commit()
    run_all(db)
    assert t.status == TaskStatus.WAITING_APPROVAL
    approval = db.scalar(select(AgentApproval).where(AgentApproval.task_id == t.id))
    assert approval.details["to"] == "piet@bakkerij.example" and "nog geen vaststaand bedrag" in \
        approval.details["body"]
    decide(db, approval, staff, approve=True, note=None)
    db.commit()
    run_all(db)
    assert t.status == TaskStatus.COMPLETED and t.output["sent"] and t.output["live"] is False  # MOCK provider

    client.contact_email = None
    t2 = enqueue(db, "customer_success", "client_update", {"client_id": client.id}, title="u2")
    db.commit()
    run_all(db)
    assert t2.status == TaskStatus.COMPLETED and not t2.output["sent"] and "geen e-mailadres" in t2.output["summary"]


def test_finance_counts_only_received_money(world):
    db, staff, client = world
    finding = _confirmed_finding(db, client, staff)
    case = case_service.create_case(db, client, [finding], staff)
    case_service.set_recovered_amount(db, case, Decimal("100"), "CN-1", staff)
    db.commit()
    enqueue(db, "finance", "finance_report", {}, title="f")
    db.commit()
    run_all(db)
    out = tasks_by_agent(db)["finance"].output
    assert Decimal(out["recovered"]) == Decimal("100") and Decimal(out["success_fee"]) == Decimal("20")
    assert Decimal(out["payout"]) == Decimal("80")
    assert out["sections"][0]["rows"][0][0] == "Bakkerij Test B.V."


def test_analytics_and_qa_reports(world):
    db, staff, client = world
    from tests.test_workflow import contact, lead

    p = lead(db)
    c = contact(db, p)
    draft = outreach_copy.initial_email(company="X", sector=None, sector_label=None, city=None,
                                        multi_location_text=None, contact_name=None, contact_role=None,
                                        generic_mailbox=True)
    crm.create_message(db, p, c, subject="s", body="Ik hoop dat het goed met u gaat.\n" + draft.body,
                       personalization=[])
    db.commit()
    enqueue(db, "analytics", "analytics_report", {}, title="a")
    enqueue(db, "qa", "review_output", {}, title="q")
    db.commit()
    run_all(db)
    t = tasks_by_agent(db)
    assert t["analytics"].status == TaskStatus.COMPLETED and t["analytics"].output["sections"]
    qa = t["qa"].output
    assert qa["issues"] >= 1 and any("verboden formulering" in r[2] for r in qa["sections"][0]["rows"])
    assert db.scalar(select(OutreachMessage)).body.startswith("Ik hoop")  # QA reports, changes nothing


def test_orchestrator_requeues_stuck_tasks_and_plans_the_day(world):
    db, staff, client = world
    stuck = enqueue(db, "analytics", "analytics_report", {}, title="stuck")
    stuck.status, stuck.started_at = TaskStatus.RUNNING, utcnow() - timedelta(hours=2)
    orch = enqueue(db, "orchestrator", "plan_workflow", {"goal": "daily"}, title="daily", priority=9)
    db.commit()
    execute(db, claim_next(db))
    db.refresh(orch)
    assert orch.status == TaskStatus.COMPLETED
    assert stuck.status == TaskStatus.QUEUED
    planned = {t.agent_id for t in db.scalars(select(AgentTask).where(AgentTask.parent_task_id == orch.id))}
    assert {"follow_up", "qa", "finance", "analytics"} <= planned


def test_orchestrator_starts_lead_generation(world):
    db, staff, client = world
    orch = enqueue(db, "orchestrator", "plan_workflow", {"goal": "find_leads", "target": 3}, title="leads")
    db.commit()
    run_all(db)
    t = tasks_by_agent(db)
    assert orch.status == TaskStatus.COMPLETED
    assert t["lead_researcher"].workflow_id == "lead_generation" and t["outreach"].status == TaskStatus.COMPLETED
