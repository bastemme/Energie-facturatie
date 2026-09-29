"""Lead Researcher: finds Dutch businesses with an energy-intensive profile in public sources.

Flow: validate input → (approval for large jobs) → search per sector → read company websites → deduplicate →
apply size and keyword criteria → score with a documented heuristic → save leads with evidence → hand
qualifying ones to the Lead Qualifier.

The fit score is a transparent heuristic (see SECTORS and score_candidate), not a prediction; every point
has a written reason. No personal data is collected here (that is the Contact Researcher's job).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agents.base import Agent, AgentError
from app.agents.catalog import AGENTS
from app.agents.context import ExecutionContext
from app.config import get_settings
from app.domain.enums import LeadStage
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
    Sector("food", "Voedselproductie", ('["man_made"="works"]["product"~"food|meat|dairy|cheese|milk|bread|beverage|'
                                        'beer|sugar|chocolate|potato|vegetable|fish",i]',
                                        '["industrial"~"food|dairy|brewery|slaughterhouse|bakery"]',
                                        '["craft"="brewery"]'),
           "zeer hoog", 45, "koelen, verwarmen en processen die dag en nacht draaien"),
    Sector("chemicals", "Chemie en kunststoffen", ('["industrial"~"chemical|plastic"]',
                                                  '["man_made"="works"]["product"~"chemical|plastic|paint|rubber",i]'),
           "zeer hoog", 45, "procesverwarming, persluchten en continu draaiende installaties"),
    Sector("paper", "Papier en verpakkingen", ('["industrial"~"paper|packaging|printing"]',
                                              '["man_made"="works"]["product"~"paper|cardboard|packaging",i]'),
           "zeer hoog", 45, "drogen, persen en machines met hoog vermogen"),
    Sector("cold_storage", "Koel- en vrieshuizen", ('["industrial"~"cold_storage|refrigerat"]',
                                                    '["building"="cold_storage"]'),
           "zeer hoog", 45, "koelen en vriezen dag en nacht"),
    Sector("logistics", "Logistiek en warehousing", ('["industrial"~"warehouse|logistics|distribution"]',
                                                     '["building"="warehouse"]["name"]', '["office"="logistics"]'),
           "hoog", 35, "grote hallen met verlichting, verwarming, laadinfrastructuur en soms koeling"),
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

# Presets for the "Find leads" form. "energy_intensive" = B2B industries that are plausible candidates for an
# energy-invoice audit; it is a selection of industries, not a statement about any company's consumption.
PRESETS = {
    "energy_intensive": ("Energie-intensieve B2B (industrie, voeding, chemie, papier, koelhuizen, logistiek)",
                         ("manufacturing", "food", "chemicals", "paper", "cold_storage", "logistics")),
    "hospitality_retail": ("Retail en horeca (supermarkten, bakkerijen, hotels, restaurants)",
                           ("supermarkets", "bakeries", "hotels", "restaurants")),
    "leisure_care": ("Zorg, zwembaden en sport", ("care", "swimming", "fitness")),
}
COUNTRY_AREAS = {"NL": "Nederland"}

MAX_LIMIT = 200
SEARCH_FACTOR = 3  # search more candidates than wanted, so criteria and deduplication still leave enough


@dataclass
class ResearchInput:
    sectors: list[Sector]
    area: str
    target: int  # total number of new leads wanted
    per_sector: int  # how many candidates to request per sector from the source
    require_website: bool
    min_locations: int
    keywords: list[str]


def validate_input(data: dict) -> ResearchInput:
    keys = list(data.get("sectors", []))
    if data.get("preset") in PRESETS:
        keys += [k for k in PRESETS[data["preset"]][1] if k not in keys]
    sectors = [SECTORS[s] for s in keys if s in SECTORS]
    if not sectors:
        raise AgentError("Kies ten minste één branche.")
    area = str(data.get("area", "")).strip() or COUNTRY_AREAS.get(str(data.get("country", "")), "")
    if not area or len(area) > 80:
        raise AgentError("Geef een gemeente, plaats of provincie op (bijv. 'Tilburg' of 'Noord-Brabant').")
    legacy = "target" not in data and "limit" in data  # older tasks: `limit` = maximum per sector
    try:
        target = int(data["limit"]) * len(sectors) if legacy else int(data.get("target") or 20)
        min_locations = int(data.get("min_locations") or 1)
    except (TypeError, ValueError) as exc:
        raise AgentError("Aantallen moeten getallen zijn.") from exc
    if not 1 <= target <= MAX_LIMIT:
        raise AgentError(f"Het aantal bedrijven ligt tussen 1 en {MAX_LIMIT}.")
    if not 1 <= min_locations <= 50:
        raise AgentError("Het minimum aantal vestigingen ligt tussen 1 en 50.")
    keywords = [k.strip().lower() for k in re.split(r"[,;\n]", str(data.get("criteria") or "")) if k.strip()][:10]
    per_sector = int(data["limit"]) if legacy else min(MAX_LIMIT, max(target, target * SEARCH_FACTOR // len(sectors)))
    return ResearchInput(sectors, area, target, per_sector, bool(data.get("require_website", False)), min_locations,
                         keywords)


def locations_from(facts: WebsiteFacts | None) -> int | None:
    if facts is None or not facts.multi_location:
        return None
    m = re.search(r"\d+", facts.multi_location)
    return int(m.group(0)) if m else None


def relevance(sector: Sector, c: BusinessCandidate, facts: WebsiteFacts | None, source_label: str) -> list[dict]:
    """'Why this company may be relevant': only statements that a source supports, each with that source.
    Nothing here claims how much energy the company uses; that is UNKNOWN until invoices are seen."""
    out = [{"text": f"Geregistreerd als {sector.label.lower()} in {source_label}. In deze branche is energie "
                    f"doorgaans een grote kostenpost ({sector.why}); het werkelijke verbruik van dit bedrijf is "
                    "onbekend.", "source": c.source_url}]
    if facts is not None and facts.multi_location:
        out.append({"text": f"De eigen website noemt '{facts.multi_location}': mogelijk meerdere aansluitingen en "
                            "facturen.", "source": facts.final_url})
    if facts is not None and facts.kvk_number:
        out.append({"text": f"KvK-nummer {facts.kvk_number} staat op de eigen website.", "source": facts.final_url})
    if c.brand:
        out.append({"text": f"Onderdeel van '{c.brand}': energie wordt mogelijk centraal ingekocht.",
                    "source": c.source_url})
    return out


def score_candidate(sector: Sector, c: BusinessCandidate, facts: WebsiteFacts | None) -> tuple[int, list[str]]:
    """Transparent fit heuristic (0–100). Each component is recorded as a reason."""
    score = sector.base_score
    reasons = [f"+{sector.base_score} branche {sector.label.lower()}: {sector.why} (branchekenmerk, geen meting "
               "bij dit bedrijf)"]
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
        inp = validate_input(ctx.input)
        if inp.target > settings.research_approval_threshold:
            ctx.require_approval(
                "large_research",
                f"Groot onderzoek: {inp.target} bedrijven in {inp.area} "
                f"({', '.join(s.label for s in inp.sectors)}). Grote zoekopdrachten belasten openbare bronnen; "
                "bevestig dat dit gewenst is.",
                {"area": inp.area, "sectors": [s.key for s in inp.sectors], "target": inp.target},
            )
        criteria = []
        if inp.min_locations > 1:
            criteria.append(f"minimaal {inp.min_locations} vestigingen (volgens de eigen website)")
        if inp.keywords:
            criteria.append(f"trefwoorden op website: {', '.join(inp.keywords)}")
        ctx.log("plan", f"Zoek {inp.target} bedrijven: {', '.join(s.label for s in inp.sectors)} in {inp.area}"
                + (f"; criteria: {'; '.join(criteria)}" if criteria else ""),
                data={"sectors": [s.key for s in inp.sectors], "area": inp.area, "target": inp.target,
                      "min_locations": inp.min_locations, "keywords": inp.keywords})
        ctx.progress(0, inp.target)

        stats = {"found": 0, "saved": 0, "duplicates": 0, "skipped_no_website": 0, "websites_read": 0,
                 "website_errors": 0, "failed_criteria": 0}
        saved: list[dict] = []
        provider_id, test_data = None, False
        fetch_budget = max(settings.research_max_website_fetches, inp.target if (inp.min_locations > 1 or inp.keywords)
                           else 0)

        # 1. Search every industry first, so the result is spread over the industries (round robin below).
        per_sector: list[tuple[Sector, list[BusinessCandidate]]] = []
        errors: list[str] = []
        provider = None
        for sector in inp.sectors:
            try:
                filters = [f + '["website"]' if inp.require_website else f for f in sector.osm_filters]
                provider, candidates = ctx.use("web.search_businesses", sector_filters=filters,
                                               area=inp.area, limit=inp.per_sector, sector=sector.key)
            except WebAccessError as exc:
                errors.append(f"{sector.label}: {exc}")
                ctx.log("search.failed", f"Zoeken naar {sector.label.lower()} mislukt: {exc}", level="WARNING")
                continue
            provider_id, test_data = provider.id, provider.is_test_data
            stats["found"] += len(candidates)
            if not candidates:
                ctx.log("search.empty", f"Geen {sector.label.lower()} gevonden in {inp.area}", level="WARNING")
            per_sector.append((sector, candidates))
        if errors and not per_sector:
            raise WebAccessError("De onderzoeksbron is niet bereikbaar: " + "; ".join(errors))

        def interleave():
            queues = [(s, list(cs)) for s, cs in per_sector]
            while any(cs for _, cs in queues):
                for s, cs in queues:
                    if cs:
                        yield s, cs.pop(0)

        # 2. Check, read and save candidates until the requested number is reached.
        for sector, c in interleave():
            if stats["saved"] >= inp.target:
                break
            if inp.require_website and not c.website:
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
            locations = locations_from(facts)
            if not self._meets_criteria(inp, facts, locations):
                stats["failed_criteria"] += 1
                continue
            score, reasons = score_candidate(sector, c, facts)
            if inp.keywords:
                reasons.append(f"criterium gevonden op website: {', '.join(inp.keywords)}")
            now = utcnow().isoformat()
            evidence = [{"label": f"Vermelding in {provider.label}", "url": c.source_url, "retrieved_at": now}]
            if facts is not None:
                evidence.append({"label": "Eigen website", "url": facts.final_url, "retrieved_at": now})
            prospect = ctx.use(
                "prospects.save", company_name=c.name[:200], sector=sector.key, street=c.street,
                postcode=c.postcode, city=c.city or inp.area, website=c.website, domain=domain, phone=c.phone,
                kvk_number=facts.kvk_number if facts else None, brand=c.brand,
                website_title=facts.title if facts else None,
                website_description=facts.description if facts else None,
                signals=(facts.signals if facts else {}), fit_score=score, fit_reasons=reasons,
                source=c.source, source_ref=c.source_ref, evidence=evidence, is_test_data=c.is_test_data,
                discovered_by_agent_id=self.spec.id, research_task_id=ctx.task.id, stage=LeadStage.NEW,
                locations_count=locations, next_action="Kwalificeren",
                relevance=relevance(sector, c, facts, provider.label),
            )
            ctx.event(prospect.id, "research", f"Gevonden via {provider.label} (fitscore {score})",
                      {"source": c.source_url})
            stats["saved"] += 1
            ctx.progress(stats["saved"], inp.target)
            saved.append({"id": prospect.id, "name": prospect.company_name, "city": prospect.city,
                          "sector": sector.key, "score": score, "website": c.website})

        threshold = settings.lead_qualify_threshold
        qualified = [p for p in sorted(saved, key=lambda p: -p["score"]) if p["score"] >= threshold]
        handoff_id = None
        if qualified and ctx.task.workflow_id:  # only continue when the task runs as part of a workflow
            workflow = get_workflow(ctx.task.workflow_id)
            nxt = workflow.next_stage(self.spec.id) if workflow else None
            if nxt is not None:
                child = ctx.handoff(
                    nxt.agent_id, nxt.task_type, {"prospect_ids": [p["id"] for p in qualified],
                                                  "source_task_id": ctx.task.id, "threshold": threshold},
                    title=f"Kwalificeer {len(qualified)} bedrijven uit {inp.area}",
                )
                handoff_id = child.id
        ctx.shared("workflow").set("prospect_ids", [p["id"] for p in saved])
        ctx.shared("global").set("lead_research.last_run", {"area": inp.area, "saved": stats["saved"],
                                                            "at": utcnow().isoformat(), "test_data": test_data})
        summary = (f"{stats['saved']} van {inp.target} gevraagde bedrijven vastgelegd in {inp.area} "
                   f"({stats['duplicates']} al bekend), {len(qualified)} boven drempel {threshold} doorgezet naar "
                   "kwalificatie")
        if stats["failed_criteria"]:
            summary += f"; {stats['failed_criteria']} voldeden niet aan de criteria"
        if test_data:
            summary += " — TESTDATA"
        return {"summary": summary, "provider": provider_id, "test_data": test_data, "area": inp.area,
                "sectors": [s.key for s in inp.sectors], "target": inp.target, **stats, "qualified": len(qualified),
                "threshold": threshold, "prospects": saved, "handoff_task_id": handoff_id}

    @staticmethod
    def _meets_criteria(inp: ResearchInput, facts: WebsiteFacts | None, locations: int | None) -> bool:
        if inp.min_locations > 1 and (locations or 1) < inp.min_locations:
            return False
        if inp.keywords:
            text = facts.text if facts else ""
            if not all(k in text for k in inp.keywords):
                return False
        return True
