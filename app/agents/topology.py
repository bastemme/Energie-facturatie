"""How the 16 agents are connected, as the code actually connects them.

Every edge here corresponds to a real call: a workflow stage handing work to the next stage (ctx.handoff),
the Orchestrator starting tasks, a human approval gate, or an agent reading another agent's output.
tests/test_observability.py checks that the workflow edges stay in sync with app/workflows/catalog.py.

The command center (ops/dashboard.html) draws exactly these edges; packets travel along them when a task is
handed over.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    kind: str  # handoff | dispatch | approval | reads
    label: str


EDGES: tuple[Edge, ...] = (
    # Invoice recovery workflow (app/workflows/catalog.py "invoice_recovery")
    Edge("invoice_intake", "invoice_analysis", "handoff", "Uitgelezen facturen"),
    Edge("invoice_analysis", "audit", "handoff", "Bevindingen met bronnen"),
    Edge("audit", "recovery", "approval", "Na bevestiging door een specialist"),
    Edge("recovery", "claims", "handoff", "Dossier per leverancier"),
    Edge("claims", "finance", "reads", "Ingediende en ontvangen bedragen"),
    Edge("claims", "customer_success", "reads", "Dossierstatus voor de klant"),
    # Lead generation workflow ("lead_generation")
    Edge("lead_researcher", "lead_qualifier", "handoff", "Onderzochte bedrijven"),
    Edge("lead_qualifier", "contact_researcher", "handoff", "Gekwalificeerde leads"),
    Edge("contact_researcher", "outreach", "handoff", "Gevonden contacten"),
    Edge("outreach", "email", "approval", "Na goedkeuring van het bericht"),
    Edge("email", "follow_up", "reads", "Onbeantwoorde e-mails"),
    # The Orchestrator starts work (Orchestrator.run: goals daily, find_leads, recover_client)
    Edge("orchestrator", "invoice_intake", "dispatch", "Terugvordering starten"),
    Edge("orchestrator", "lead_researcher", "dispatch", "Leads zoeken"),
    Edge("orchestrator", "email", "dispatch", "Mailbox lezen"),
    Edge("orchestrator", "follow_up", "dispatch", "Opvolging"),
    Edge("orchestrator", "recovery", "dispatch", "Dossiers voorbereiden"),
    Edge("orchestrator", "qa", "dispatch", "Kwaliteitscontrole"),
    Edge("orchestrator", "finance", "dispatch", "Financieel overzicht"),
    Edge("orchestrator", "analytics", "dispatch", "Prestatieoverzicht"),
)

# Where each agent sits in the command center. Lanes follow the catalog groups; the recovery lane reads as the
# product's story: invoice → understanding → verification → audit → opportunity → recovery → result.
LANES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("terugvordering", "Van factuur tot resultaat", ("invoice_intake", "invoice_analysis", "audit", "recovery",
                                                     "claims", "finance")),
    ("coordinatie", "Coördinatie", ("qa", "analytics", "orchestrator", "customer_success")),
    ("acquisitie", "Acquisitie", ("lead_researcher", "lead_qualifier", "contact_researcher", "outreach", "email",
                                  "follow_up")),
)

# The phase each recovery-lane agent represents in the story.
PHASES = {
    "invoice_intake": "Factuur", "invoice_analysis": "Begrip", "audit": "Verificatie", "recovery": "Kans",
    "claims": "Terugvordering", "finance": "Resultaat",
}


def neighbours(agent_id: str) -> dict[str, list[dict]]:
    """Incoming and outgoing edges of one agent, for the side panel."""
    return {
        "incoming": [{"agent": e.source, "kind": e.kind, "label": e.label} for e in EDGES if e.target == agent_id],
        "outgoing": [{"agent": e.target, "kind": e.kind, "label": e.label} for e in EDGES if e.source == agent_id],
    }
