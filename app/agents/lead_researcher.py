"""Lead Researcher: finds Dutch businesses with an energy-intensive profile in public sources.

Flow: validate input → (approval for large jobs) → search per sector → read company websites → deduplicate →
score with a documented heuristic → save prospects with evidence → hand qualified ones to the Lead Qualifier.

The fit score is a transparent heuristic (see SECTORS and score_candidate), not a prediction; every point
has a written reason. No personal data is collected.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.base import Agent, AgentError
from app.agents.catalog import AGENTS
from app.agents.context import ExecutionContext
from app.config import get_settings
from app.integrations.web.http import UnsafeURLError, WebAccessError
from app.integrations.web.research import BusinessCandidate
from app.integrations.web.website import WebsiteFacts, domain_of
from app.models.base import utcnow
from app.workflows.catalog import get_workflow


@dataclass(frozen=True)
class Sector:
    key: str
    label: str
    osm_filters: tuple[str, ...]  # Overpass tag filters
    energy_profile: str  # zeer hoog | hoog | gemiddeld
    base_score: int
    why: str


SECTORS: dict[str, Sector] = {s.key: s for s in [
    Sector("manufacturing", "Productie en metaalbewerking", ('["man_made"="works"]', '["craft"="metal_construction"]'),
           "zeer hoog", 45, "machines, persluchten, ovens: hoog en continu verbruik"),
    Sector("swimming", "Zwembaden en wellness", ('["leisure"="sports_centre"]["sport"="swimming"]',
                                                  '["leisure"="water_park"]', '["leisure"="sauna"]'),
           "zeer hoog", 45, "verwarming van water en lucht, pompen, ventilatie"),
    Sector("supermarkets", "Supermarkten", ('["shop"="supermarket"]',), "hoog", 35,
           "koeling en vriezers draaien dag en nacht"),
    Sector("bakeries", "Bakkerijen", ('["craft"="bakery"]', '["shop"="bakery"]'), "hoog", 35,
           "ovens en koeling, vaak gas én elektriciteit"),
    Sector("hotels", "Hotels", ('["tourism"="hotel"]',), "hoog", 35, "verwarming, warm water, keuken, wasserij"),
    Sector("laundries", "Wasserijen en stomerijen", ('["shop"="laundry"]', '["shop"="dry_cleaning"]'), "hoog", 35,
           "wassen, drogen en persen: veel gas en stroom"),
    Sector("care", "Zorginstellingen", ('["amenity"="nursing_home"]', '["social_facility"="nursing_home"]'),
           "hoog", 35, "24/7 verwarming, verlichting en keuken"),
    Sector("fitness", "Sportscholen", ('["leisure"="fitness_centre"]',), "gemiddeld", 25,
           "verlichting, ventilatie, douches"),
    Sector("restaurants", "Restaurants", ('["amenity"="restaurant"]',), "gemiddeld", 25,
           "keuken, koeling en afzuiging"),
]}

MAX_LIMIT = 200


def validate_input(data: dict) -> tuple[list[Sector], str, int, bool]:
    sectors = [SECTORS[s] for s in data.get("sectors", []) if s in SECTORS]
    if not sectors:
        raise AgentError("Kies ten minste één sector.")
    area = str(data.get("area", "")).strip()
    if not area or len(area) > 80:
        raise AgentError("Geef een gemeente, plaats of provincie op (bijv. 'Tilburg' of 'Noord-Brabant').")
    try:
        limit = int(data.get("limit", 20))
    except (TypeError, ValueError) as exc:
        raise AgentError("Het maximum aantal bedrijven moet een getal zijn.") from exc
    if not 1 <= limit <= MAX_LIMIT:
        raise AgentError(f"Het maximum per sector ligt tussen 1 en {MAX_LIMIT}.")
    return sectors, area, limit, bool(data.get("require_website", False))


def score_candidate(sector: Sector, c: BusinessCandidate, facts: WebsiteFacts | None) -> tuple[int, list[str]]:
    """Transparent fit heuristic (0–100). Each component is recorded as a reason."""
    score = sector.base_score
    reasons = [f"+{sector.base_score} sector {sector.label.lower()}: energieprofiel {sector.energy_profile} "
               f"({sector.why})"]
    if c.website:
        score += 10
        reasons.append("+10 heeft een eigen website (vindbaar, controleerbaar)")
    if facts is not None and facts.status_code < 400:
        score += 5
        reasons.append("+5 website bereikbaar en gelezen")
    if facts is not None and facts.kvk_number:
        score += 10
        reasons.append(f"+10 KvK-nummer {facts.kvk_number} vermeld op de website")
    if facts is not None and facts.multi_location:
        score += 15
        reasons.append(f"+15 meerdere locaties genoemd ('{facts.multi_location}'): meer aansluitingen en facturen")
    if c.street and (c.city or c.postcode):
        score += 5
        reasons.append("+5 volledig adres bekend")
    if c.brand:
        score -= 15
        reasons.append(f"−15 filiaal van '{c.brand}': energie wordt waarschijnlijk centraal ingekocht")
    return max(0, min(100, score)), reasons


class LeadResearcher(Agent):
    spec = next(a for a in AGENTS if a.id == "lead_researcher")

    def run(self, ctx: ExecutionContext) -> dict:
        settings = get_settings()
        sectors, area, limit, require_website = validate_input(ctx.input)
        total_requested = limit * len(sectors)
        if total_requested > settings.research_approval_threshold:
            ctx.require_approval(
                "large_research",
                f"Groot onderzoek: tot {total_requested} bedrijven in {area} ({', '.join(s.label for s in sectors)}). "
                "Grote zoekopdrachten belasten openbare bronnen; bevestig dat dit gewenst is.",
                {"area": area, "sectors": [s.key for s in sectors], "limit": limit},
            )
        ctx.log("plan", f"Onderzoek {', '.join(s.label for s in sectors)} in {area}, max. {limit} per sector",
                data={"sectors": [s.key for s in sectors], "area": area, "limit": limit})

        stats = {"found": 0, "saved": 0, "duplicates": 0, "skipped_no_website": 0, "websites_read": 0,
                 "website_errors": 0}
        saved: list[dict] = []
        provider_id, test_data = None, False
        fetch_budget = settings.research_max_website_fetches

        for sector in sectors:
            provider, candidates = ctx.use("web.search_businesses", sector_filters=list(sector.osm_filters),
                                           area=area, limit=limit, sector=sector.key)
            provider_id, test_data = provider.id, provider.is_test_data
            stats["found"] += len(candidates)
            if not candidates:
                ctx.log("search.empty", f"Geen {sector.label.lower()} gevonden in {area}", level="WARNING")
            for c in candidates:
                if require_website and not c.website:
                    stats["skipped_no_website"] += 1
                    continue
                domain = domain_of(c.website)
                dup = ctx.use("prospects.find_duplicate", source_ref=c.source_ref, domain=domain, name=c.name,
                              city=c.city)
                if dup is not None:
                    stats["duplicates"] += 1
                    continue
                facts = None
                if c.website and fetch_budget > 0:
                    fetch_budget -= 1
                    try:
                        facts = ctx.use("web.fetch_website", url=c.website)
                        stats["websites_read"] += 1
                    except (WebAccessError, UnsafeURLError) as exc:
                        stats["website_errors"] += 1
                        ctx.log("website.unreachable", f"{c.name}: {exc}", level="WARNING")
                score, reasons = score_candidate(sector, c, facts)
                now = utcnow().isoformat()
                evidence = [{"label": f"Vermelding in {provider.label}", "url": c.source_url, "retrieved_at": now}]
                if facts is not None:
                    evidence.append({"label": "Eigen website", "url": facts.final_url, "retrieved_at": now})
                prospect = ctx.use(
                    "prospects.save", company_name=c.name[:200], sector=sector.key, street=c.street,
                    postcode=c.postcode, city=c.city or area, website=c.website, domain=domain, phone=c.phone,
                    kvk_number=facts.kvk_number if facts else None, brand=c.brand,
                    website_title=facts.title if facts else None,
                    website_description=facts.description if facts else None,
                    signals=(facts.signals if facts else {}), fit_score=score, fit_reasons=reasons,
                    source=c.source, source_ref=c.source_ref, evidence=evidence, is_test_data=c.is_test_data,
                    discovered_by_agent_id=self.spec.id, research_task_id=ctx.task.id,
                )
                stats["saved"] += 1
                saved.append({"id": prospect.id, "name": prospect.company_name, "city": prospect.city,
                              "sector": sector.key, "score": score, "website": c.website})

        threshold = settings.lead_qualify_threshold
        qualified = [p for p in sorted(saved, key=lambda p: -p["score"]) if p["score"] >= threshold]
        handoff_id = None
        if qualified:
            workflow = get_workflow(ctx.task.workflow_id or "lead_generation")
            nxt = workflow.next_stage(self.spec.id) if workflow else None
            if nxt is not None:
                child = ctx.handoff(
                    nxt.agent_id, nxt.task_type, {"prospect_ids": [p["id"] for p in qualified],
                                                  "source_task_id": ctx.task.id, "threshold": threshold},
                    title=f"Kwalificeer {len(qualified)} bedrijven uit {area}",
                )
                handoff_id = child.id
        ctx.shared("workflow").set("prospect_ids", [p["id"] for p in saved])
        ctx.shared("global").set("lead_research.last_run", {"area": area, "saved": stats["saved"],
                                                            "at": utcnow().isoformat(), "test_data": test_data})
        summary = (f"{stats['saved']} bedrijven vastgelegd in {area} ({stats['duplicates']} al bekend), "
                   f"{len(qualified)} boven drempel {threshold} doorgezet naar kwalificatie")
        if test_data:
            summary += " — TESTDATA"
        return {"summary": summary, "provider": provider_id, "test_data": test_data, "area": area,
                "sectors": [s.key for s in sectors], **stats, "qualified": len(qualified), "threshold": threshold,
                "prospects": saved, "handoff_task_id": handoff_id}
