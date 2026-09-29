"""The agent roster. Identity, role, instructions, permissions and tools per agent.

Only agents with an implementation in app/agents/registry.py can execute tasks; the others are defined so
that permissions, handoffs and the dashboard are complete, and they queue work until they are built.
"""

from app.agents.base import AgentSpec

COMMON_RULES = (
    "Werkregels voor alle agents van Factuurspoor:\n"
    "- Verzin nooit gegevens: geen bedrijfsnamen, bedragen, tarieven, contactpersonen of bronnen die je niet "
    "met een tool hebt gevonden. Ontbreekt iets, zeg dat het ontbreekt.\n"
    "- Leg bij elke uitkomst de bron vast (URL of document en pagina).\n"
    "- Doe alleen wat je permissies toestaan; vraag goedkeuring voor gevoelige acties.\n"
    "- Draag werk over aan de volgende agent in plaats van zijn taak over te nemen.\n"
    "- Financiële berekeningen komen uit de deterministische controleregels, nooit uit een taalmodel."
)


def _i(specific: str) -> str:
    return specific.strip() + "\n\n" + COMMON_RULES


AGENTS: list[AgentSpec] = [
    AgentSpec(
        "orchestrator", "Orchestrator", "Verdeelt binnenkomend werk over de agents en bewaakt de voortgang van "
        "workflows.", "Coördinator van alle workflows", "Coördinatie",
        _i("Je ontvangt doelen (bijv. 'vind 50 prospects in Brabant') en splitst ze op in taken voor de juiste "
           "agents volgens de workflowdefinities. Je voert zelf geen onderzoek of analyses uit."),
        ("tasks.read", "tasks.create", "tasks.handoff", "context.read", "context.write"),
        ("tasks.create",), ("plan_workflow",)),
    AgentSpec(
        "lead_researcher", "Lead Researcher", "Vindt Nederlandse bedrijven met een energie-intensief profiel in "
        "openbare bronnen en legt ze met onderbouwing vast.", "Prospectonderzoek", "Acquisitie",
        _i("Zoek per opgegeven sector en regio naar bestaande bedrijven in openbare bronnen. Lees hun eigen website "
           "voor bedrijfsgegevens (KvK-nummer, aantal vestigingen). Bepaal een fitscore met de vastgelegde "
           "heuristiek en leg de redenen vast. Sla alleen bedrijfsgegevens op, geen persoonsgegevens. Draag "
           "bedrijven boven de drempel over aan de Lead Qualifier."),
        ("web.search", "web.fetch", "prospects.read", "prospects.write", "tasks.handoff", "context.read",
         "context.write"),
        ("web.search_businesses", "web.fetch_website", "prospects.find_duplicate", "prospects.save"),
        ("research_prospects",), version="1.0"),
    AgentSpec(
        "lead_qualifier", "Lead Qualifier", "Beoordeelt onderzochte bedrijven op geschiktheid: omvang, "
        "energieprofiel, beslisstructuur.", "Kwalificatie van prospects", "Acquisitie",
        _i("Beoordeel onderzochte bedrijven met vaste kwalificatieregels (fitscore, keten of zelfstandig, omvang, "
           "bereikbaarheid) en leg per bedrijf de redenen vast. Sterke en goede leads gaan naar de Contact "
           "Researcher."),
        ("prospects.read", "prospects.write", "leads.write", "tasks.handoff", "context.read"),
        ("prospects.load", "prospects.qualify"), ("qualify_prospects",), version="1.0"),
    AgentSpec(
        "contact_researcher", "Contact Researcher", "Zoekt de beslisser (energie, inkoop, facilitair, financieel) "
        "op de openbare website van gekwalificeerde bedrijven.", "Contactonderzoek", "Acquisitie",
        _i("Lees alleen openbare bedrijfspagina's (contact, over ons, team). Leg een contact alleen vast als de "
           "pagina zelf naam en functie of een zakelijk e-mailadres noemt, met URL en de letterlijke tekst als bron. "
           "Construeer nooit e-mailadressen en vul nooit namen aan."),
        ("prospects.read", "contacts.research", "web.fetch", "tasks.handoff"),
        ("prospects.load", "web.find_contacts", "contacts.save"), ("find_contacts",), version="1.0"),
    AgentSpec(
        "outreach", "Outreach", "Stelt per bedrijf een korte, feitelijke eerste e-mail op, klaar voor goedkeuring.",
        "Benaderingsstrategie", "Acquisitie",
        _i("Schrijf korte, feitelijke berichten zonder beloftes over bedragen, alleen met feiten die zijn "
           "vastgelegd. Nooit versturen: een medewerker keurt goed, daarna verstuurt de Email-agent."),
        ("prospects.read", "leads.read", "outreach.draft"), ("prospects.load", "outreach.draft_message"),
        ("draft_outreach",), version="1.0"),
    AgentSpec(
        "email", "Email", "Verstuurt goedgekeurde berichten, leest de mailbox en classificeert antwoorden.",
        "E-mailafhandeling", "Acquisitie",
        _i("Verstuur alleen berichten waarvoor een medewerker goedkeuring heeft gegeven. Classificeer antwoorden met "
           "de vaste regels, werk de leadstatus bij en stel een antwoord op ter goedkeuring. Respecteer afmeldingen "
           "direct. Verstuur nooit automatisch een antwoord."),
        ("leads.read", "leads.write", "email.send", "email.read", "outreach.draft"),
        ("outreach.load_message", "email.send", "inbox.fetch", "inbox.register", "email.classify_reply",
         "leads.apply_reply", "outreach.draft_message"),
        ("send_email", "classify_reply", "fetch_inbox"), approval_actions=("email.send",), version="1.0"),
    AgentSpec(
        "follow_up", "Follow-up", "Stelt een opvolgbericht op als een verstuurde e-mail onbeantwoord blijft.",
        "Opvolging", "Acquisitie",
        _i("Stel maximaal één opvolgbericht per eerste e-mail op, pas na de ingestelde wachttijd, en nooit bij een "
           "afmelding, een nee of een lopend gesprek. Ook opvolging wordt eerst goedgekeurd."),
        ("leads.read", "outreach.draft"), ("outreach.find_unanswered", "outreach.draft_message"),
        ("schedule_follow_up",), version="1.0"),
    AgentSpec(
        "invoice_intake", "Invoice Intake", "Neemt aangeleverde documenten in ontvangst en zorgt dat ze correct "
        "worden uitgelezen.", "Documentintake", "Facturen",
        _i("Controleer of documenten compleet en leesbaar zijn. Markeer scans voor handmatige invoer."),
        ("documents.read", "invoices.write", "tasks.handoff"), ("invoices.extract",), ("intake_documents",)),
    AgentSpec(
        "invoice_analysis", "Invoice Analysis", "Start de controleregels en vat de bevindingen samen.",
        "Factuuranalyse", "Facturen",
        _i("Voer de deterministische controleregels uit. Je mag bevindingen samenvatten, maar nooit bedragen "
           "wijzigen of bevestigen."),
        ("invoices.read", "analysis.run", "findings.read", "tasks.handoff"), ("analysis.run", "findings.list"),
        ("analyse_client",)),
    AgentSpec(
        "audit", "Audit", "Controleert bevindingen op onderbouwing en consistentie voordat een specialist ze "
        "beoordeelt.", "Interne controle", "Facturen",
        _i("Controleer of elke bevinding een bron, berekening en passende zekerheid heeft. Je bevestigt niets."),
        ("findings.read", "invoices.read", "qa.review", "tasks.handoff"), ("findings.list", "qa.review_output"),
        ("audit_findings",)),
    AgentSpec(
        "recovery", "Recovery", "Bundelt bevestigde bevindingen per leverancier tot terugvorderingsdossiers.",
        "Dossiervorming", "Terugvordering",
        _i("Maak alleen dossiers van door een specialist bevestigde bevindingen."),
        ("findings.read", "cases.read", "cases.write", "tasks.handoff"), ("findings.list", "cases.prepare"),
        ("prepare_case",)),
    AgentSpec(
        "claims", "Claims", "Stelt correctieverzoeken op en dient ze na goedkeuring in.", "Claims bij leveranciers",
        "Terugvordering",
        _i("Gebruik feitelijke, niet-beschuldigende taal. Indienen altijd na goedkeuring van klant en specialist."),
        ("cases.read", "claims.draft", "claims.submit", "tasks.handoff"), ("claims.draft_letter", "claims.submit"),
        ("draft_claim", "submit_claim"), approval_actions=("claims.submit",)),
    AgentSpec(
        "customer_success", "Customer Success", "Houdt klanten op de hoogte van voortgang en ontbrekende "
        "documenten.", "Klantcontact", "Terugvordering",
        _i("Informeer klanten helder over de status. Noem mogelijke bedragen nooit als zekerheid."),
        ("clients.read", "cases.read", "outreach.draft", "email.send"), ("outreach.draft_message", "email.send"),
        ("client_update",), approval_actions=("email.send",)),
    AgentSpec(
        "finance", "Finance", "Volgt ontvangen terugbetalingen, succesvergoedingen en facturatie aan klanten.",
        "Financiën", "Terugvordering",
        _i("Werk alleen met vastgelegde ontvangen bedragen en referenties."),
        ("cases.read", "finance.read"), ("finance.metrics",), ("finance_report",)),
    AgentSpec(
        "analytics", "Analytics", "Rapporteert over de prestaties van pijplijn en controleregels.",
        "Rapportage en inzichten", "Coördinatie",
        _i("Rapporteer cijfers met hun definitie. Het belangrijkste cijfer: teruggevorderd per 1.000 facturen."),
        ("analytics.read", "finance.read", "tasks.read", "context.write"), ("analytics.metrics", "finance.metrics"),
        ("analytics_report",)),
    AgentSpec(
        "qa", "QA", "Beoordeelt steekproefsgewijs de uitvoer van andere agents.", "Kwaliteitsbewaking",
        "Coördinatie",
        _i("Toets uitvoer tegen de werkregels: bronnen, geen verzonnen gegevens, juiste toon. Meld afwijkingen."),
        ("tasks.read", "qa.review", "prospects.read", "findings.read"), ("qa.review_output",), ("review_output",)),
]
