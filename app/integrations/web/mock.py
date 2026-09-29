"""Development/test provider. Clearly synthetic: never mistaken for production results.

Every candidate is marked is_test_data=True, names start with [TESTDATA] and websites use the reserved
.example domain, which can never resolve (RFC 2606).
"""

from __future__ import annotations

from app.integrations.web.research import BusinessCandidate

_NAMES = {
    "manufacturing": "Metaalbewerking", "swimming": "Zwembad", "supermarkets": "Supermarkt", "bakeries": "Bakkerij",
    "hotels": "Hotel", "laundries": "Wasserij", "care": "Zorgcentrum", "fitness": "Sportschool",
    "restaurants": "Restaurant", "food": "Voedingsbedrijf", "chemicals": "Chemiebedrijf", "paper": "Verpakkingsbedrijf",
    "cold_storage": "Koelhuis", "logistics": "Logistiekbedrijf",
}
_STREETS = ["Hoofdstraat 12", "Industrieweg 4", "Stationsplein 1", "Ambachtsweg 9", "Marktplein 3", "Sportlaan 20",
            "Havenweg 88", "Lindeplein 7"]


class MockResearchProvider:
    id = "mock"
    label = "Testdata (geen internet)"
    is_test_data = True

    def search_businesses(self, sector_filters: list[str], area: str, limit: int, sector: str
                          ) -> list[BusinessCandidate]:
        kind = _NAMES.get(sector, sector.capitalize())
        out = []
        for i in range(min(limit, len(_STREETS))):
            slug = f"{kind.lower()}-{area.lower().replace(' ', '-')}-{i + 1}"
            out.append(BusinessCandidate(
                name=f"[TESTDATA] {kind} {area} {i + 1}", sector=sector, source="mock",
                source_ref=f"mock:{sector}:{area.lower()}:{i}", source_url=f"https://{slug}.example/",
                street=_STREETS[i], postcode=f"50{10 + i} AB", city=area,
                website=f"https://{slug}.example" if i % 4 != 3 else None,
                brand="Voorbeeldketen" if i == 5 else None, is_test_data=True,
            ))
        return out


_TEST_PEOPLE = [("Sanne Voorbeeld", "Financieel manager"), ("Mark Testmans", "Energiemanager"),
                ("Eva Proefstra", "Inkoopmanager"), ("Daan Demoweg", "Facilitair manager")]


def mock_contacts(website: str, company_name: str = ""):
    """Test stand-in for the Contact Researcher on .example domains. Some companies deliberately have no
    named decision maker, so the 'nothing found' path is exercised too. Everything is marked as test data."""
    from app.integrations.web.contacts import ContactFinding, ContactSearchResult, role_category
    from app.integrations.web.website import domain_of

    domain = domain_of(website) or "test.example"
    n = sum(map(ord, domain))
    page = website.rstrip("/") + "/contact"
    findings = [ContactFinding(None, "Algemeen contactadres", "GENERAL", f"info@{domain}", "GENERAL_COMPANY_EMAIL",
                               None, page,
                               f"[TESTDATA] E-mailadres op de website: info@{domain}", "LOW", is_test_data=True)]
    if n % 3 != 0:
        name, role = _TEST_PEOPLE[n % len(_TEST_PEOPLE)]
        local = name.lower().replace(" ", ".")
        findings.insert(0, ContactFinding(
            name, role, role_category(role), f"{local}@{domain}", "PERSONAL_BUSINESS",
            None, page, f"[TESTDATA] {name} – {role} – {local}@{domain}", "HIGH", is_test_data=True))
    return ContactSearchResult(pages_read=[website, page], findings=findings, errors=[])


def mock_website_facts(url: str):
    """Deterministic stand-in for reading a website on the reserved .example domain (tests/development)."""
    from app.integrations.web.website import WebsiteFacts

    multi = "3 vestigingen" if url.rstrip("/").endswith(("-1.example", "-3.example")) else None
    text = "[testdata] voorbeeldwebsite testpagina zonder echte bedrijfsgegevens productie" + (
        f" {multi}" if multi else "")
    return WebsiteFacts(url=url, final_url=url, status_code=200, title="[TESTDATA] Voorbeeldwebsite",
                        description="Testpagina zonder echte bedrijfsgegevens", kvk_number=None,
                        multi_location=multi, signals={"kvk_on_site": False, "multi_location": multi,
                                                       "test_data": True}, text=text)
