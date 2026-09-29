"""Agent permissions. Least privilege: an agent can only use tools whose permission it holds."""

from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    "tasks.read": "Taken en resultaten van andere agents lezen",
    "tasks.create": "Nieuwe taken aanmaken",
    "tasks.handoff": "Werk overdragen aan een volgende agent",
    "context.read": "Gedeelde context lezen",
    "context.write": "Gedeelde context schrijven",
    "web.search": "Openbare bronnen doorzoeken",
    "web.fetch": "Openbare websites ophalen",
    "prospects.read": "Onderzochte bedrijven lezen",
    "prospects.write": "Onderzochte bedrijven opslaan en bijwerken",
    "leads.read": "Leads lezen",
    "leads.write": "Leads aanmaken en bijwerken",
    "contacts.research": "Zakelijke contactgegevens onderzoeken",
    "outreach.draft": "Benaderingsberichten opstellen",
    "email.send": "E-mail versturen (altijd na menselijke goedkeuring)",
    "documents.read": "Klantdocumenten lezen",
    "invoices.read": "Factuurgegevens lezen",
    "invoices.write": "Factuurgegevens vastleggen",
    "analysis.run": "Controleregels uitvoeren",
    "findings.read": "Bevindingen lezen",
    "cases.read": "Dossiers lezen",
    "cases.write": "Dossiers voorbereiden",
    "claims.draft": "Claimbrieven opstellen",
    "claims.submit": "Claims indienen bij leveranciers (altijd na menselijke goedkeuring)",
    "clients.read": "Klantgegevens lezen",
    "finance.read": "Financiële cijfers lezen",
    "analytics.read": "Analyses en statistieken lezen",
    "qa.review": "Werk van andere agents beoordelen",
}

# Actions that always pause for a human decision, whichever agent performs them.
ALWAYS_APPROVE = {"email.send", "claims.submit"}


class PermissionDenied(Exception):
    pass


def require(agent_permissions: tuple[str, ...] | list[str], permission: str) -> None:
    if permission not in PERMISSIONS:
        raise PermissionDenied(f"Onbekende permissie: {permission}")
    if permission not in agent_permissions:
        raise PermissionDenied(f"Agent heeft geen permissie '{permission}'")
