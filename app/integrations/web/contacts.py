"""Find business decision makers on a company's own public website.

Reads the homepage and a few linked pages (contact, over ons, team, organisatie). A contact is recorded only
when the page itself states it: a name next to a recognised role, a business e-mail address on the company's
own domain, or a LinkedIn profile linked from the site. The exact text is kept as evidence. Nothing is guessed:
no e-mail patterns are constructed, no names are completed.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from app.config import get_settings
from app.integrations.web import http
from app.integrations.web.website import domain_of

ROLE_PATTERNS: dict[str, tuple[str, ...]] = {
    "ENERGY": ("energiemanager", "energie manager", "energy manager", "manager energie", "energiecoördinator",
               "energiecoordinator", "duurzaamheidsmanager", "sustainability manager"),
    "CFO": ("cfo", "financieel directeur", "chief financial officer", "directeur financiën", "directeur financien"),
    "FINANCE": ("financieel manager", "finance manager", "financial manager", "manager financiën", "hoofd financiën",
                "hoofd financien", "financial controller", "controller", "hoofd administratie"),
    "PROCUREMENT": ("inkoopmanager", "manager inkoop", "hoofd inkoop", "procurement manager", "purchasing manager",
                    "inkoper", "buyer"),
    "FACILITY": ("facilitair manager", "facility manager", "facilities manager", "manager facilitair",
                 "hoofd facilitaire zaken", "hoofd facilitair"),
    "OPERATIONS": ("operationeel manager", "operations manager", "operationeel directeur", "plant manager",
                   "vestigingsmanager", "technisch manager", "hoofd technische dienst", "bedrijfsleider"),
    "MANAGEMENT": ("algemeen directeur", "managing director", "directeur-eigenaar", "directeur eigenaar",
                   "directeur", "eigenaar", "ceo", "owner"),
}
ROLE_LABELS = {"ENERGY": "Energiemanager", "CFO": "CFO / financieel directeur", "FINANCE": "Financieel manager",
               "PROCUREMENT": "Inkoopmanager", "FACILITY": "Facilitair manager", "OPERATIONS": "Operationeel manager",
               "MANAGEMENT": "Directie", "GENERAL": "Algemeen contactadres"}
_ROLE_RE = re.compile(r"\b(" + "|".join(sorted({re.escape(r) for rs in ROLE_PATTERNS.values() for r in rs},
                                                 key=len, reverse=True)) + r")\b", re.I)
_PARTICLES = r"(?:van|de|der|den|het|ter|ten|te|in|'t|op|la|le|du|el|al)"
_NAME_RE = re.compile(rf"\b([A-Z][a-zà-ÿ'’-]+(?:\s+(?:{_PARTICLES}\s+)*[A-Z][a-zà-ÿ'’-]+){{1,2}})\b")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LINK_RE = re.compile(r"<a\s[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_BLOCK = re.compile(r"</?(?:p|div|li|br|h[1-6]|tr|td|section|article|footer|header)[^>]*>", re.I)
_TAGS = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>|<[^>]+>", re.I | re.S)
_PAGE_HINTS = ("contact", "over-ons", "over ons", "overons", "team", "organisatie", "about", "wie-zijn-wij",
               "wie zijn wij", "management", "directie", "medewerkers")
GENERIC_LOCAL = {"info", "contact", "hallo", "hello", "sales", "verkoop", "administratie", "office", "kantoor",
                 "receptie", "service", "support", "klantenservice", "boekhouding", "facturen", "finance", "inkoop",
                 "welkom", "mail", "algemeen"}
_NOT_NAMES = {"Onze", "Ons", "Neem", "Contact", "Wij", "Welkom", "Bel", "Mail", "Heeft", "Meer", "Lees", "Over",
              "Team", "Home", "Nieuws", "Vacatures", "Privacy", "Cookie", "Algemene", "Voorwaarden", "Bekijk",
              "Openingstijden", "Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag",
              "Route", "Adres", "Telefoon", "Email", "Directie", "Management", "Manager", "Directeur", "Eigenaar",
              "Energie", "Inkoop", "Facilitair", "Financieel", "Controller", "Hoofd", "Chief", "Officer", "Onderneming",
              "Bedrijf", "Stuur", "Vraag", "Klik", "Hier", "Nederland", "Kvk", "Btw", "Iban", "Linkedin"}


@dataclass
class ContactFinding:
    full_name: str | None
    role: str | None
    role_category: str
    email: str | None
    email_type: str | None
    linkedin_url: str | None
    source_url: str
    excerpt: str
    confidence: str  # HIGH | MEDIUM | LOW
    is_test_data: bool = False


@dataclass
class ContactSearchResult:
    pages_read: list[str]
    findings: list[ContactFinding]
    errors: list[str]


def page_text(body: str) -> str:
    text = _TAGS.sub(" ", _BLOCK.sub("\n", body))
    lines = (" ".join(html.unescape(line).split()) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def role_category(role_text: str) -> str:
    low = role_text.lower()
    for cat, patterns in ROLE_PATTERNS.items():
        if any(p in low for p in patterns):
            return cat
    return "GENERAL"


def _clean_name(name: str, company_words: set[str]) -> str | None:
    words = name.split()
    caps = [w for w in words if w[:1].isupper()]
    if len(caps) < 2 or any(w in _NOT_NAMES or w.lower() in company_words for w in caps):
        return None
    return name.strip()


def extract_contacts(page_url: str, body: str, company_domain: str | None, company_name: str = "") -> list[
        ContactFinding]:
    """Pure function: contacts stated on one page. Tested with fixture HTML."""
    text = page_text(body)
    company_words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", company_name)}
    emails = []
    for e in dict.fromkeys(m.group(0).rstrip(".") for m in _EMAIL_RE.finditer(html.unescape(body))):
        dom = e.split("@", 1)[1].lower()
        if company_domain and (dom == company_domain or dom.endswith("." + company_domain)):
            emails.append(e.lower())
    linkedin = [(href, " ".join(page_text(label).split())) for href, label in _LINK_RE.findall(body)
                if "linkedin.com/in/" in href]
    findings: list[ContactFinding] = []
    seen = set()
    for line in text.split("\n"):
        for rm in _ROLE_RE.finditer(line):
            role = rm.group(1)
            window_start, window_end = max(0, rm.start() - 70), min(len(line), rm.end() + 70)
            candidates = []
            for nm in _NAME_RE.finditer(line, window_start, window_end):
                if nm.start() <= rm.start() < nm.end():
                    continue
                name = _clean_name(nm.group(1), company_words)
                if name:
                    dist = rm.start() - nm.end() if nm.end() <= rm.start() else nm.start() - rm.end()
                    candidates.append((dist, name))
            if not candidates:
                continue
            name = min(candidates)[1]
            key = (name.lower(), role.lower())
            if key in seen:
                continue
            seen.add(key)
            email = _email_for(name, emails)
            li = next((href for href, label in linkedin if name.lower() in label.lower()
                       or _slug(name) in href.lower()), None)
            findings.append(ContactFinding(
                full_name=name, role=role[:1].upper() + role[1:], role_category=role_category(role), email=email,
                email_type="PERSONAL_BUSINESS" if email else None, linkedin_url=li, source_url=page_url,
                excerpt=line[max(0, window_start - 20):window_end + 20][:300],
                confidence="HIGH" if email else "MEDIUM"))
    used = {f.email for f in findings if f.email}
    for e in emails:
        local = e.split("@", 1)[0]
        if e in used or local not in GENERIC_LOCAL:
            continue
        findings.append(ContactFinding(
            full_name=None, role=ROLE_LABELS["GENERAL"], role_category="GENERAL", email=e,
            email_type="GENERAL_COMPANY_EMAIL",
            linkedin_url=None, source_url=page_url, excerpt=f"E-mailadres op de website: {e}", confidence="LOW"))
    return findings


def _slug(name: str) -> str:
    return "-".join(name.lower().split())


def _email_for(name: str, emails: list[str]) -> str | None:
    parts = [p.lower() for p in name.split() if len(p) > 2 and not re.fullmatch(_PARTICLES, p.lower())]
    for e in emails:
        local = e.split("@", 1)[0]
        if local in GENERIC_LOCAL:
            continue
        if parts and (parts[0] in local or parts[-1] in local):
            return e
    return None


def candidate_pages(base_url: str, body: str) -> list[str]:
    host = urlparse(base_url).hostname
    out = []
    for href, label in _LINK_RE.findall(body):
        url = urljoin(base_url, href.strip())
        target = f"{href} {page_text(label)}".lower()
        if urlparse(url).hostname == host and any(h in target for h in _PAGE_HINTS) and url not in out:
            out.append(url.split("#")[0])
    for path in ("/contact", "/over-ons", "/team"):
        guess = urljoin(base_url, path)
        if guess not in out:
            out.append(guess)
    return out


def find_contacts(website: str, company_name: str = "") -> ContactSearchResult:
    domain = domain_of(website)
    if (urlparse(website).hostname or "").endswith(".example"):
        from app.integrations.web.mock import mock_contacts

        return mock_contacts(website, company_name)
    limit = get_settings().contact_max_pages
    home = http.get(website)
    pages, errors, findings = [home.url], [], extract_contacts(home.url, home.text, domain, company_name)
    for url in candidate_pages(home.url, home.text):
        if len(pages) >= limit:
            break
        if url in pages:
            continue
        try:
            resp = http.get(url)
        except (http.WebAccessError, http.UnsafeURLError) as exc:
            errors.append(f"{url}: {exc}")
            continue
        if resp.status_code >= 400:
            continue
        pages.append(resp.url)
        findings.extend(extract_contacts(resp.url, resp.text, domain, company_name))
    return ContactSearchResult(pages_read=pages, findings=_dedupe(findings), errors=errors)


def _dedupe(findings: list[ContactFinding]) -> list[ContactFinding]:
    best: dict[tuple, ContactFinding] = {}
    rank = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
    for f in findings:
        key = ("name", f.full_name.lower()) if f.full_name else ("mailbox", f.email or "")
        if key not in best or rank[f.confidence] > rank[best[key].confidence]:
            best[key] = f
    return list(best.values())
