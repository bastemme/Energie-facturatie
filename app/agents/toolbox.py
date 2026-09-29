"""All tools available to agents. Implemented tools have a handler; planned tools are declared so the
dashboard and permission model already know them."""

from __future__ import annotations

from app.agents.tools import ToolSpec, register
from app.integrations.web.research import get_research_provider
from app.integrations.web.website import fetch_website
from app.services import prospects


def _search(sector_filters: list[str], area: str, limit: int, sector: str):
    provider = get_research_provider()
    return provider, provider.search_businesses(sector_filters, area, limit, sector)


register(ToolSpec(
    "web.search_businesses", "Zoekt bedrijven per sector en regio in een openbare bron (standaard OpenStreetMap).",
    "web.search", _search,
    summarize=lambda r: {"provider": r[0].id, "results": len(r[1]), "test_data": r[0].is_test_data},
))
register(ToolSpec(
    "web.fetch_website", "Haalt de openbare website van een bedrijf op en leest titel, omschrijving, KvK-nummer en "
    "signalen over meerdere vestigingen.", "web.fetch", fetch_website,
    summarize=lambda f: {"status": f.status_code, "kvk": bool(f.kvk_number), "multi_location": bool(f.multi_location)},
))
register(ToolSpec("prospects.find_duplicate", "Zoekt of een bedrijf al eerder is onderzocht (bron, domein, naam).",
                  "prospects.read", prospects.find_duplicate, needs_db=True,
                  summarize=lambda p: {"duplicate": p is not None}))
register(ToolSpec("prospects.save", "Slaat een onderzocht bedrijf op, met bron en onderbouwing.", "prospects.write",
                  prospects.save_prospect, needs_db=True, summarize=lambda p: {"prospect_id": p.id}))

# Planned tools — declared now so permissions and the dashboard are complete; implemented with their agent.
for name, desc, perm in [
    ("kvk.lookup", "Controleert bedrijfsgegevens in het KvK Handelsregister (API-sleutel nodig).", "web.search"),
    ("contacts.find_business_contact", "Zoekt een zakelijk contactpunt op basis van openbare bedrijfsinformatie.",
     "contacts.research"),
    ("outreach.draft_message", "Stelt een persoonlijk benaderingsbericht op.", "outreach.draft"),
    ("email.send", "Verstuurt een e-mail via de gekoppelde mailbox.", "email.send"),
    ("leads.create", "Maakt een lead aan vanuit een gekwalificeerd bedrijf.", "leads.write"),
    ("invoices.extract", "Leest geüploade facturen uit.", "invoices.write"),
    ("analysis.run", "Voert de controleregels uit voor een klant.", "analysis.run"),
    ("findings.list", "Leest bevindingen en hun onderbouwing.", "findings.read"),
    ("cases.prepare", "Bereidt een terugvorderingsdossier voor.", "cases.write"),
    ("claims.draft_letter", "Stelt een correctieverzoek aan de leverancier op.", "claims.draft"),
    ("claims.submit", "Dient een claim in bij de leverancier.", "claims.submit"),
    ("finance.metrics", "Leest omzet, succesvergoedingen en kosten.", "finance.read"),
    ("analytics.metrics", "Leest prestatie-indicatoren van het platform.", "analytics.read"),
    ("qa.review_output", "Beoordeelt de uitvoer van een andere agent tegen kwaliteitsregels.", "qa.review"),
    ("tasks.create", "Maakt taken aan voor andere agents.", "tasks.create"),
]:
    register(ToolSpec(name, desc, perm))
