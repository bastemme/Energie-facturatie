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


# Dutch provinces by ISO 3166-2 code, busiest (most businesses) first. A search for the whole country is split
# into one small query per province: public Overpass servers often answer a single nationwide query with 504.
PROVINCES = ["NL-NB", "NL-ZH", "NL-NH", "NL-GE", "NL-UT", "NL-OV", "NL-LI", "NL-FR", "NL-GR", "NL-DR", "NL-FL",
             "NL-ZE"]
COUNTRY_NAMES = {"nederland", "netherlands", "the netherlands", "nl"}
RETRY_STATUS = {429, 502, 503, 504}


def build_query(sector_filters: list[str], area: str, limit: int, iso: str | None = None) -> str:
    if iso:
        area_sel = f'area["ISO3166-2"="{iso}"]["boundary"="administrative"]->.a;'
    else:
        area_esc = area.replace("\\", "").replace('"', "")
        area_sel = f'area["name"="{area_esc}"]["boundary"="administrative"]["admin_level"~"^(4|7|8|10)$"]->.a;'
    selectors = "\n".join(f"  nwr{f}[\"name\"](area.a);" for f in sector_filters)
    return f"[out:json][timeout:60];\n{area_sel}\n(\n{selectors}\n);\nout center tags {int(limit)};"


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


def overpass_endpoints() -> list[str]:
    s = get_settings()
    extra = [u.strip() for u in (s.overpass_fallback_urls or "").split(",") if u.strip()]
    return list(dict.fromkeys([s.overpass_url, *extra]))


class OpenStreetMapProvider:
    id = "openstreetmap"
    label = "OpenStreetMap (Overpass API)"
    is_test_data = False

    def search_businesses(self, sector_filters: list[str], area: str, limit: int, sector: str
                          ) -> list[BusinessCandidate]:
        if area.strip().lower() not in COUNTRY_NAMES:
            return parse_elements(self._query(build_query(sector_filters, area, limit)), sector)[:limit]
        # Whole country: province by province (rotated per industry for a spread), until enough are found.
        start = sum(map(ord, sector)) % 4
        order = PROVINCES[start:] + PROVINCES[:start]
        found: dict[str, BusinessCandidate] = {}
        errors: list[str] = []
        for iso in order:
            if len(found) >= limit:
                break
            try:
                payload = self._query(build_query(sector_filters, area, limit - len(found), iso=iso))
            except http.WebAccessError as exc:
                errors.append(f"{iso}: {exc}")
                continue
            for c in parse_elements(payload, sector):
                found.setdefault(c.source_ref, c)
        if not found and errors and len(errors) == len(order):
            raise http.WebAccessError("OpenStreetMap niet bereikbaar voor alle provincies: " + errors[0])
        return list(found.values())[:limit]

    def _query(self, query: str) -> dict:
        """POST to the Overpass servers in turn; a busy server (429/5xx/timeout) is skipped for the next one."""
        problems = []
        for url in overpass_endpoints():
            host = url.split("/")[2] if "//" in url else url
            try:
                resp = http.post_form(url, {"data": query})
            except http.WebAccessError as exc:
                problems.append(f"{host}: {exc}")
                continue
            if resp.status_code in RETRY_STATUS:
                problems.append(f"{host}: {'overbelast' if resp.status_code == 429 else 'time-out'} "
                                f"(status {resp.status_code})")
                continue
            if resp.status_code >= 400:
                raise http.WebAccessError(f"OpenStreetMap ({host}) gaf status {resp.status_code}")
            try:
                payload = json.loads(resp.text)
            except json.JSONDecodeError:
                problems.append(f"{host}: geen geldig antwoord")
                continue
            remark = str(payload.get("remark") or "")
            if "error" in remark.lower():
                problems.append(f"{host}: {remark[:120]}")
                continue
            return payload
        raise http.WebAccessError("OpenStreetMap-servers zijn overbelast of onbereikbaar ("
                                  + "; ".join(problems) + "). Probeer het over een paar minuten opnieuw.")
