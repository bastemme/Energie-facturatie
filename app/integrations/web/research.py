"""Research provider interface. Agents depend on this, never on a concrete source."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class BusinessCandidate:
    name: str
    sector: str
    source: str  # provider id
    source_ref: str  # stable id at the source, e.g. osm:node/123
    source_url: str  # where a human can verify it
    street: str | None = None
    postcode: str | None = None
    city: str | None = None
    website: str | None = None
    phone: str | None = None
    brand: str | None = None
    extra: dict = field(default_factory=dict)
    is_test_data: bool = False


class ResearchProvider(Protocol):
    id: str
    label: str
    is_test_data: bool

    def search_businesses(self, sector_filters: list[str], area: str, limit: int, sector: str
                          ) -> list[BusinessCandidate]: ...


def get_research_provider(name: str | None = None) -> ResearchProvider:
    from app.config import get_settings

    name = name or get_settings().research_provider
    if name == "openstreetmap":
        from app.integrations.web.openstreetmap import OpenStreetMapProvider

        return OpenStreetMapProvider()
    if name == "mock":
        from app.integrations.web.mock import MockResearchProvider

        return MockResearchProvider()
    raise ValueError(f"Onbekende onderzoeksbron: {name}")
