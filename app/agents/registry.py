"""Agent registry: specs, implementations, and syncing identity to the database."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.base import Agent, AgentSpec
from app.agents.catalog import AGENTS
from app.domain.enums import AgentStatus
from app.models import AgentRecord

_SPECS: dict[str, AgentSpec] = {a.id: a for a in AGENTS}


def _implementations() -> dict[str, Agent]:
    from app.agents.lead_researcher import LeadResearcher
    from app.agents.pipeline import ContactResearcher, EmailAgent, FollowUpAgent, LeadQualifier, OutreachAgent

    return {"lead_researcher": LeadResearcher(), "lead_qualifier": LeadQualifier(),
            "contact_researcher": ContactResearcher(), "outreach": OutreachAgent(), "email": EmailAgent(),
            "follow_up": FollowUpAgent()}


def get_spec(agent_id: str) -> AgentSpec | None:
    return _SPECS.get(agent_id)


def all_specs() -> list[AgentSpec]:
    return list(_SPECS.values())


def get_implementation(agent_id: str) -> Agent | None:
    return _implementations().get(agent_id)


def is_implemented(agent_id: str) -> bool:
    return agent_id in _implementations()


def sync_agents(db: Session) -> None:
    """Create/update agent rows from code. Runtime state (status, enabled, last run) is preserved."""
    impls = _implementations()
    for spec in AGENTS:
        rec = db.get(AgentRecord, spec.id)
        if rec is None:
            rec = AgentRecord(id=spec.id, status=AgentStatus.IDLE, enabled=True)
            db.add(rec)
        rec.name, rec.description, rec.role, rec.group = spec.name, spec.description, spec.role, spec.group
        rec.system_instructions = spec.system_instructions
        rec.permissions, rec.tools = list(spec.permissions), list(spec.tools)
        rec.definition_version = spec.version
        rec.implemented = spec.id in impls
    db.commit()
