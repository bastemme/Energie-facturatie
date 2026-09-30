"""DEMO MODE: a scripted run of one fictitious invoice through Factuurspoor.

Development only (not available when ER_ENVIRONMENT=production). The scenario uses the same event schema and
the same case/metrics shape as the live observability layer, so the command center renders it with exactly the
same components. Every event has mode="demo"; nothing is written to the database, and all names and amounts
are fictitious (the supplier is literally called "Voorbeeld Energie B.V. (fictief)").

The numbers are internally consistent:
    this invoice   312.450 kWh × € 0,1248 = € 38.993,76 (+ € 9.297,56 other costs = € 48.291,32)
    difference     € 0,1248 − € 0,1182 = € 0,0066 per kWh
    affected       312.450 kWh (this invoice) + 1.578.620 kWh (6 earlier invoices) = 1.891.070 kWh
    recovery       1.891.070 kWh × € 0,0066 = € 12.481,06
"""

from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import timedelta

from app.models.base import utcnow
from app.services.observability import CASE_AGENTS, EVIDENCE_NODES, STAGES, Event, _iso

AMOUNT = 12481.06
BASE_METRICS = {"invoices": 1284, "analyses": 0, "potential": 184320.00, "validated": 96112.40, "cases_open": 14,
                "cases_recovered": 9}
DURATION_MS = 30000


class _Case:
    """Mutable case state; every change is emitted as a full case object (same shape as live_case)."""

    def __init__(self):
        self.stages = {k: "pending" for k, _ in STAGES}
        self.stages["received"] = "active"
        self.agents = {a: {"agent": a, "state": "pending", "note": None} for a in CASE_AGENTS}
        self.evidence = {k: [] for k, _ in EVIDENCE_NODES}
        self.confidence = "Laag"
        self.finding = None
        self.validated = False
        self.potential = 0.0
        self.status = "Ontvangen"
        self.current = "received"

    def stage(self, key: str, state: str = "done") -> None:
        self.stages[key] = state
        nxt = next((k for k, _ in STAGES if self.stages[k] != "done"), None)
        if nxt and self.stages[nxt] == "pending":
            self.stages[nxt] = "active"
        self.current = nxt

    def agent(self, agent_id: str, state: str, note: str | None = None) -> None:
        self.agents[agent_id] = {"agent": agent_id, "state": state, "note": note}

    def view(self) -> dict:
        sources = sum(len(v) for v in self.evidence.values())
        evidence = None
        if sources or self.finding:
            evidence = {"title": self.finding or "Bewijs wordt verzameld", "amount": self.potential,
                        "confidence": self.confidence, "validated": self.validated, "sources": sources,
                        "nodes": [{"key": k, "label": label, "items": self.evidence[k][:4],
                                   "count": len(self.evidence[k])} for k, label in EVIDENCE_NODES],
                        "href": None}
        return copy.deepcopy({
            "client": "Demo: fictieve klant", "synthetic": True,
            "invoice": {"number": "2026-08421", "supplier": "Voorbeeld Energie B.V. (fictief)", "amount": 48291.32},
            "status": self.status, "current": self.current,
            "stages": [{"key": k, "label": label, "state": self.stages[k]} for k, label in STAGES],
            "agents": list(self.agents.values()),
            "opportunity": {"potential": self.potential, "validated": AMOUNT if self.validated else 0.0,
                            "findings": 1 if self.finding else 0, "confirmed": 1 if self.validated else 0},
            "evidence": evidence, "href": None,
        })


def scenario() -> dict:
    now = utcnow()
    events: list[Event] = []
    case = _Case()
    m = dict(BASE_METRICS)
    tasks: dict[str, dict] = {}
    n = 0

    def ev(at, kind, agent, message, *, to=None, task=None, level="INFO", data=None, with_case=False,
           with_metrics=False):
        nonlocal n
        n += 1
        payload = dict(data or {})
        if with_case:
            payload["case"] = case.view()
        if with_metrics:
            payload["metrics"] = dict(m)
        t = tasks.get(task) if isinstance(task, str) else task
        events.append(Event(f"demo:{n}", _iso(now + timedelta(milliseconds=at)), kind, agent, message, mode="demo",
                            to=to, task=t, level=level, data=payload, at=at))

    def task(key, number, title):
        tasks[key] = {"id": f"demo-{key}", "number": number, "title": title}
        return key

    # ---- a fictitious invoice arrives
    ev(0, "demo.start", "orchestrator", "Demo gestart: één fictieve factuur loopt door Factuurspoor",
       with_case=True, with_metrics=True)
    task("orc", 9001, "Nieuwe factuur 2026-08421 indelen")
    ev(500, "task.started", "orchestrator", "Nieuwe factuur 2026-08421 indelen", task="orc")
    ev(1200, "task.step", "orchestrator", "Workflow Terugvordering gekozen", task="orc")
    task("intake", 9002, "Factuur 2026-08421 verwerken")
    ev(1800, "handoff", "orchestrator", "Terugvordering starten: 1 document", to="invoice_intake", task="intake")
    ev(1900, "task.completed", "orchestrator", "1 taak ingepland voor doel 'recover_client'", task="orc")

    # ---- a lead-generation stream in parallel: several data streams at once
    task("res", 9003, "Vind 25 leads: voedingsmiddelenindustrie, Noord-Brabant")
    ev(1400, "task.started", "lead_researcher", "Vind 25 leads: voedingsmiddelenindustrie, Noord-Brabant", task="res")
    ev(2600, "task.step", "lead_researcher", "Openbare bron doorzocht: 38 bedrijven gevonden", task="res")
    ev(3400, "task.progress", "lead_researcher", "21 / 38", task="res", data={"done": 21, "total": 38})
    ev(4200, "task.progress", "lead_researcher", "38 / 38", task="res", data={"done": 38, "total": 38})
    task("qual", 9004, "Kwalificeer 31 bedrijven")
    ev(4600, "handoff", "lead_researcher", "31 bedrijven boven de fitdrempel", to="lead_qualifier", task="qual")
    ev(4700, "task.completed", "lead_researcher", "31 bedrijven vastgelegd met bron", task="res")
    ev(5400, "task.started", "lead_qualifier", "Kwalificeer 31 bedrijven", task="qual")
    ev(6800, "task.step", "lead_qualifier", "Vaste kwalificatieregels toegepast: 12 sterk of goed", task="qual")
    task("con", 9005, "Zoek beslissers bij 12 bedrijven")
    ev(7600, "handoff", "lead_qualifier", "12 gekwalificeerde leads", to="contact_researcher", task="con")
    ev(7700, "task.completed", "lead_qualifier", "12 van 31 gekwalificeerd", task="qual")
    ev(8500, "task.started", "contact_researcher", "Zoek beslissers bij 12 bedrijven", task="con")
    ev(10600, "task.progress", "contact_researcher", "12 / 12", task="con", data={"done": 12, "total": 12})
    task("out", 9006, "Stel e-mails op voor 7 bedrijven")
    ev(11800, "handoff", "contact_researcher", "7 zakelijke adressen van de eigen website", to="outreach",
       task="out")
    ev(11900, "task.completed", "contact_researcher", "7 contacten gevonden, 5 bedrijven zonder openbaar adres",
       task="con")
    ev(12700, "task.started", "outreach", "Stel e-mails op voor 7 bedrijven", task="out")
    ev(14600, "task.completed", "outreach", "7 concepten klaar voor goedkeuring (niets verstuurd)", task="out")

    # ---- intake: the document is read
    case.agent("invoice_intake", "active", "Document ontvangen")
    m["analyses"] = 1
    ev(2500, "task.started", "invoice_intake", "Factuur 2026-08421 verwerken", task="intake", with_case=True,
       with_metrics=True)
    ev(3100, "task.step", "invoice_intake", "Document ontvangen: factuur-2026-08421.pdf (3 pagina's)", task="intake")
    for i, at in enumerate((3700, 4200, 4700), start=1):
        ev(at, "task.progress", "invoice_intake", f"Pagina {i} / 3 gelezen", task="intake",
           data={"done": i, "total": 3})
    case.agent("invoice_intake", "done", "184 factuurregels uitgelezen")
    case.agent("invoice_analysis", "queued", "Wacht op uitgelezen regels")
    case.stage("received")
    case.stage("extracted")
    case.status = "Analyseren"
    m["invoices"] += 1
    ev(5200, "task.step", "invoice_intake", "184 factuurregels uitgelezen; EAN, periode en tarieven herkend",
       task="intake")
    task("ana", 9007, "Controleer factuur 2026-08421")
    ev(5700, "handoff", "invoice_intake", "184 factuurregels", to="invoice_analysis", task="ana")
    ev(5800, "task.completed", "invoice_intake", "1 factuur uitgelezen: 184 regels", task="intake", with_case=True,
       with_metrics=True)

    # ---- analysis: evidence is gathered, confidence grows
    case.agent("invoice_analysis", "active", "Controleregels uitvoeren")
    ev(6500, "task.started", "invoice_analysis", "Controleer factuur 2026-08421", task="ana", with_case=True)
    found = [
        (7100, "contract", "Contract gekoppeld", "Leveringsovereenkomst art. 4.2: € 0,1182 per kWh", "Laag"),
        (8100, "tariff", "Tarief per factuurregel vergeleken met het contract",
         "Factuurregel p. 1, r. 1: 312.450 kWh × € 0,1248", "Laag"),
        (9100, "consumption", "Verbruik vergeleken met meterdata", "Meterdata netbeheerder aug. 2026: 312.450 kWh",
         "Middel"),
        (9600, "consumption", None, "Meterstand vorige periode sluit aan", "Middel"),
        (10500, "history", "Eerdere facturen van hetzelfde contract vergeleken",
         "6 facturen feb.–jul. 2026: zelfde tarief € 0,1248", "Middel"),
        (11300, "external", "Publieke tarieven gecontroleerd",
         "Energiebelasting 2026 (Belastingdienst): juist toegepast", "Hoog"),
        (11700, "tariff", None, "Leveringsvoorwaarden: geen indexatie in 2026", "Hoog"),
    ]
    for at, node, step, item, conf in found:
        if step:
            ev(at - 250, "task.step", "invoice_analysis", step, task="ana")
        case.evidence[node].append(item)
        case.confidence = conf
        ev(at, "evidence.found", "invoice_analysis", item, task="ana", data={"node": node, "confidence": conf},
           with_case=True)
    case.finding = "Leveringstarief hoger dan contract (7 facturen)"
    case.potential = AMOUNT
    case.stage("analyzed")
    case.stage("finding")
    case.status = "Controleren"
    case.agent("invoice_analysis", "done", "1 bevinding: € 12.481,06 mogelijk")
    case.agent("audit", "queued", "Wacht op bevinding")
    m["potential"] = round(m["potential"] + AMOUNT, 2)
    ev(12300, "finding.created", "invoice_analysis", "Bevinding: leveringstarief € 0,0066 per kWh hoger dan contract",
       task="ana", data={"amount": AMOUNT, "sources": 7, "confidence": "Hoog"}, with_case=True, with_metrics=True)
    task("aud", 9008, "Interne controle bevinding 2026-08421")
    ev(12900, "handoff", "invoice_analysis", "Bevinding met 7 bronnen", to="audit", task="aud")
    m["analyses"] = 1
    ev(13000, "task.completed", "invoice_analysis", "1 factuur gecontroleerd: 1 bevinding", task="ana")

    # ---- audit: source, calculation and confidence are checked
    case.agent("audit", "active", "Bronnen controleren")
    ev(13700, "task.started", "audit", "Interne controle bevinding 2026-08421", task="aud", with_case=True)
    ev(14300, "task.step", "audit", "Elke bewering heeft een bron (7 / 7)", task="aud")
    ev(14400, "task.progress", "audit", "7 / 7", task="aud", data={"done": 7, "total": 7})
    ev(15300, "task.step", "audit", "Berekening nagerekend: 1.891.070 kWh × € 0,0066 = € 12.481,06", task="aud")
    ev(16200, "task.step", "audit", "Zekerheid past bij de bronnen: hoog", task="aud")
    case.stage("audited")
    case.status = "Wacht op specialist"
    case.agent("audit", "done", "Bevinding consistent; wacht op specialist")
    m["analyses"] = 0
    ev(16800, "task.completed", "audit", "1 bevinding gecontroleerd, geen aandachtspunten", task="aud",
       with_case=True, with_metrics=True)

    # ---- a specialist confirms (in the real product always a person)
    case.validated = True
    case.stage("validation")
    case.status = "Dossier voorbereiden"
    m["validated"] = round(m["validated"] + AMOUNT, 2)
    ev(18000, "finding.validated", "audit", "Specialist bevestigt de bevinding (in de demo gesimuleerd)",
       data={"amount": AMOUNT}, with_case=True, with_metrics=True)

    # ---- recovery: one case per supplier
    task("rec", 9009, "Dossier voorbereiden: Voorbeeld Energie B.V.")
    ev(18700, "handoff", "audit", "Bevestigde bevinding", to="recovery", task="rec")
    case.agent("recovery", "active", "Bevindingen bundelen")
    ev(19400, "task.started", "recovery", "Dossier voorbereiden: Voorbeeld Energie B.V.", task="rec",
       with_case=True)
    ev(20100, "task.step", "recovery", "Bevestigde bevindingen gebundeld per leverancier", task="rec")
    ev(20900, "task.step", "recovery", "Terug te vorderen: € 12.481,06 (7 facturen)", task="rec",
       data={"amount": AMOUNT})
    case.stage("recovery")
    case.status = "Claim opstellen"
    case.agent("recovery", "done", "Dossier FS-DEMO-0142 aangemaakt")
    m["cases_open"] += 1
    task("clm", 9010, "Claimbrief FS-DEMO-0142")
    ev(21600, "handoff", "recovery", "Dossier FS-DEMO-0142", to="claims", task="clm")
    ev(21700, "task.completed", "recovery", "1 dossier aangemaakt: FS-DEMO-0142", task="rec", with_case=True,
       with_metrics=True)

    # ---- claims: letter drafted, submission waits for a person
    case.agent("claims", "active", "Claimbrief opstellen")
    ev(22400, "task.started", "claims", "Claimbrief FS-DEMO-0142", task="clm", with_case=True)
    ev(23200, "task.step", "claims", "Feitelijke claimbrief opgesteld, 7 bronnen als bijlage", task="clm")
    case.stage("claim")
    case.status = "Claim voorbereid"
    case.agent("claims", "done", "Indienen wacht op goedkeuring")
    ev(24000, "approval.requested", "claims", "Indienen bij de leverancier wacht op goedkeuring van een medewerker",
       task="clm", level="WARNING")
    ev(24100, "task.completed", "claims", "Claimbrief FS-DEMO-0142 opgesteld", task="clm", with_case=True)
    ev(24600, "handoff", "claims", "Geclaimd bedrag (nog niet ontvangen)", to="finance")
    ev(24800, "handoff", "claims", "Dossierstatus voor de klant", to="customer_success")

    # ---- platform agents close the loop
    task("qa", 9011, "Kwaliteitscontrole run 2026-08421")
    ev(25000, "handoff", "orchestrator", "Kwaliteitscontrole", to="qa", task="qa")
    ev(25600, "task.started", "qa", "Kwaliteitscontrole run 2026-08421", task="qa")
    ev(26500, "task.step", "qa", "Werkregels getoetst: bronnen, berekening, toon", task="qa")
    ev(27300, "task.completed", "qa", "Geen afwijkingen van de werkregels", task="qa")
    task("fin", 9012, "Financieel overzicht")
    ev(25500, "task.started", "finance", "Financieel overzicht", task="fin")
    ev(26800, "task.completed", "finance", "Geclaimd bijgewerkt; ontvangen blijft € 0 tot betaling", task="fin")
    task("ana2", 9013, "Prestatieoverzicht")
    ev(26000, "handoff", "orchestrator", "Prestatieoverzicht", to="analytics", task="ana2")
    ev(26600, "task.started", "analytics", "Prestatieoverzicht", task="ana2")
    ev(27800, "task.completed", "analytics", "Teruggevorderd per 1.000 facturen bijgewerkt", task="ana2")

    case.status = "Klaar voor indiening"
    ev(28600, "demo.end", "orchestrator", "Dossier compleet. Klaar voor indiening na goedkeuring.", with_case=True,
       with_metrics=True)
    events.sort(key=lambda e: e.at)
    return {"mode": "demo", "duration_ms": DURATION_MS, "events": [asdict(e) for e in events]}
