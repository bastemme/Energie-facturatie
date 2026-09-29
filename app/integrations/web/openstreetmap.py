"""OpenStreetMap (Overpass API): real, public business data without an API key.

OSM data is © OpenStreetMap contributors, ODbL. We store the OSM reference and link as evidence.
Usage policy: a few queries per task, identifying User-Agent, results capped.
"""

from __future__ import annotations

import json

from app.config import get_settings
from app.integrations.web import http
from app.integrations.web.research import BusinessCandidate

NL_BBOX = (50.70, 3.30, 53.60, 7.30)  # south, west, north, east


def build_query(sector_filters: list[str], area: str, limit: int) -> str:
    area_esc = area.replace("\\", "").replace('"', "")
    selectors = "\n".join(f"  nwr{f}[\"name\"](area.a);" for f in sector_filters)
    return (
        "[out:json][timeout:90];\n"
        f'area["name"="{area_esc}"]["boundary"="administrative"]["admin_level"~"^(2|4|7|8|10)$"]->.a;\n'
        f"(\n{selectors}\n);\n"
        f"out center tags {int(limit)};"
    )


def _in_nl(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return True
    s, w, n, e = NL_BBOX
    return s <= lat <= n and w <= lon <= e


def parse_elements(payload: dict, sector: str) -> list[BusinessCandidate]:
    out = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        center = el.get("center") or {}
        lat, lon = el.get("lat", center.get("lat")), el.get("lon", center.get("lon"))
        if not _in_nl(lat, lon):
            continue
        kind, ident = el.get("type"), el.get("id")
        street = " ".join(p for p in (tags.get("addr:street"), tags.get("addr:housenumber")) if p) or None
        website = tags.get("website") or tags.get("contact:website") or tags.get("url")
        if website and not website.startswith(("http://", "https://")):
            website = "https://" + website
        out.append(BusinessCandidate(
            name=name, sector=sector, source="openstreetmap", source_ref=f"osm:{kind}/{ident}",
            source_url=f"https://www.openstreetmap.org/{kind}/{ident}",
            street=street, postcode=tags.get("addr:postcode"), city=tags.get("addr:city"),
            website=website, phone=tags.get("phone") or tags.get("contact:phone"),
            brand=tags.get("brand"),
            extra={"lat": lat, "lon": lon},
        ))
    return out


class OpenStreetMapProvider:
    id = "openstreetmap"
    label = "OpenStreetMap (Overpass API)"
    is_test_data = False

    def search_businesses(self, sector_filters: list[str], area: str, limit: int, sector: str
                          ) -> list[BusinessCandidate]:
        url = get_settings().overpass_url
        resp = http.post_form(url, {"data": build_query(sector_filters, area, limit)})
        if resp.status_code == 429:
            raise http.WebAccessError("OpenStreetMap is tijdelijk overbelast (429). Probeer het later opnieuw.")
        if resp.status_code >= 400:
            raise http.WebAccessError(f"OpenStreetMap gaf status {resp.status_code}")
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise http.WebAccessError("OpenStreetMap gaf geen geldig antwoord") from exc
        remark = str(payload.get("remark") or "")
        if "error" in remark.lower():
            raise http.WebAccessError(f"OpenStreetMap kon de zoekopdracht niet afronden: {remark[:200]}")
        return parse_elements(payload, sector)[:limit]
