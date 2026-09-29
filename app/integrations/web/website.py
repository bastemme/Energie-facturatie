"""Read a company's own website: title, description, KvK number and multi-location signals.

Only company-level facts are extracted. No personal names or personal e-mail addresses (GDPR) — finding a
contact person is the Contact Researcher's job, on a documented lawful basis.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.integrations.web import http

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_DESC = re.compile(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{0,400})', re.I)
_KVK = re.compile(r"(?:kvk|kamer van koophandel|coc|chamber of commerce)[^0-9]{0,30}(\d{8})\b", re.I)
_MULTI = re.compile(r"\b(\d{1,3}\s+(?:vestigingen|locaties|filialen|winkels|hotels)|onze vestigingen|alle locaties|"
                    r"vestigingen in)\b", re.I)
_TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.I | re.S)


@dataclass
class WebsiteFacts:
    url: str
    final_url: str
    status_code: int
    title: str | None = None
    description: str | None = None
    kvk_number: str | None = None
    multi_location: str | None = None
    signals: dict = field(default_factory=dict)
    text: str = ""  # visible page text (lower case), used for keyword criteria; not stored


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "://" in url else "https://" + url).hostname or ""
    return host.lower().removeprefix("www.") or None


def analyse_html(url: str, final_url: str, status: int, body: str) -> WebsiteFacts:
    facts = WebsiteFacts(url=url, final_url=final_url, status_code=status)
    if m := _TITLE.search(body):
        facts.title = html.unescape(" ".join(m.group(1).split()))[:300] or None
    if m := _META_DESC.search(body):
        facts.description = html.unescape(" ".join(m.group(1).split()))[:400] or None
    text = html.unescape(_TAGS.sub(" ", body))
    if m := _KVK.search(text):
        facts.kvk_number = m.group(1)
    if m := _MULTI.search(text):
        facts.multi_location = " ".join(m.group(1).split())
    facts.signals = {"kvk_on_site": bool(facts.kvk_number), "multi_location": facts.multi_location}
    facts.text = " ".join(f"{facts.title or ''} {facts.description or ''} {text}".split()).lower()[:60000]
    return facts


def fetch_website(url: str) -> WebsiteFacts:
    if (urlparse(url).hostname or "").endswith(".example"):  # RFC 2606 test domain: never on the internet
        from app.integrations.web.mock import mock_website_facts

        return mock_website_facts(url)
    resp = http.get(url)
    return analyse_html(url, resp.url, resp.status_code, resp.text if "html" in resp.content_type or not
                        resp.content_type else "")
