"""Workflow definitions. A handoff follows the next stage of the task's workflow."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    agent_id: str
    task_type: str
    label: str
    needs_approval: bool = False


@dataclass(frozen=True)
class Workflow:
    id: str
    name: str
    description: str
    stages: tuple[Stage, ...]

    def next_stage(self, agent_id: str) -> Stage | None:
        ids = [s.agent_id for s in self.stages]
        if agent_id not in ids:
            return None
        i = ids.index(agent_id)
        return self.stages[i + 1] if i + 1 < len(self.stages) else None


WORKFLOWS = {
    "lead_generation": Workflow(
        "lead_generation", "Leadgeneratie", "Van openbaar onderzoek tot eerste gesprek.",
        (Stage("lead_researcher", "research_prospects", "Onderzoek"),
         Stage("lead_qualifier", "qualify_prospects", "Kwalificatie"),
         Stage("contact_researcher", "find_contacts", "Contactonderzoek"),
         Stage("outreach", "draft_outreach", "Bericht opstellen"),
         Stage("email", "send_email", "Versturen", needs_approval=True),
         Stage("follow_up", "schedule_follow_up", "Opvolging"))),
    "invoice_recovery": Workflow(
        "invoice_recovery", "Terugvordering", "Van aangeleverde factuur tot ontvangen geld.",
        (Stage("invoice_intake", "intake_documents", "Intake"),
         Stage("invoice_analysis", "analyse_client", "Analyse"),
         Stage("audit", "audit_findings", "Interne controle"),
         Stage("recovery", "prepare_case", "Dossier", needs_approval=True),  # after a specialist confirmed findings
         Stage("claims", "draft_claim", "Claimbrief"),
         Stage("finance", "finance_report", "Afrekening"))),
}


def get_workflow(workflow_id: str | None) -> Workflow | None:
    return WORKFLOWS.get(workflow_id or "")
