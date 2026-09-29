"""Deterministic mapping of Dutch invoice line descriptions to normalized categories.

Order matters: more specific rules (tax, feed-in) come before generic ones (electricity).
"""

import re

from app.domain.enums import LineCategory as C
from app.domain.units import normalize_unit

# (pattern, category, confidence)
_RULES: list[tuple[re.Pattern[str], C, float]] = [
    (re.compile(r"vermindering\s+energiebelasting|belastingvermindering|heffingskorting"), C.TAX_REDUCTION, 0.95),
    (re.compile(r"energiebelasting|\beb\b|opslag duurzame energie|\bode\b"), C.ENERGY_TAX_ELECTRICITY, 0.9),
    (re.compile(r"teruglever|terug geleverd|invoeding"), C.ELECTRICITY_FEED_IN, 0.9),
    (re.compile(r"meterhuur|meetdienst|meetvergoeding|meetkosten"), C.METER_RENTAL, 0.95),
    (re.compile(r"capaciteit"), C.NETWORK_CAPACITY, 0.9),
    (re.compile(r"kw\s?max|kwmax|gecontracteerd\s+(?:transport)?vermogen|transportdienst|transportkosten"),
     C.NETWORK_TRANSPORT, 0.85),
    (re.compile(r"netbeheer|aansluitdienst|aansluittarief|aansluitvergoeding|systeemdienst"), C.NETWORK_FIXED, 0.85),
    (re.compile(r"vaste\s+leveringskosten|vastrecht|vaste\s+kosten|abonnement|vaste\s+vergoeding"),
     C.FIXED_SUPPLY_FEE, 0.9),
    (re.compile(r"korting"), C.DISCOUNT, 0.85),
    (re.compile(r"correctie|creditering|herberekening|herfacturering"), C.CORRECTION, 0.8),
    (re.compile(r"toeslag|profielkosten|garanties? van oorsprong|\bgvo\b|balancering|opslag"), C.SURCHARGE, 0.8),
    (re.compile(r"\bdal(?:tarief|uren)?\b|laag\s*tarief|laagtarief"), C.ELECTRICITY_LOW, 0.9),
    (re.compile(r"normaal(?:tarief)?|\bpiek(?:tarief|uren)?\b|hoog\s*tarief|hoogtarief"), C.ELECTRICITY_NORMAL, 0.9),
    (re.compile(r"enkel(?:tarief)?"), C.ELECTRICITY_SINGLE, 0.9),
]

_GAS = re.compile(r"\bgas\b|aardgas|gasverbruik|levering gas")
_ELEC = re.compile(r"elektriciteit|stroom|levering\s+elektr|electricity")


def classify_line(description: str, unit: str | None = None) -> tuple[C, float]:
    text = description.lower()
    u = normalize_unit(unit)
    for pattern, category, conf in _RULES:
        if pattern.search(text):
            if category == C.ENERGY_TAX_ELECTRICITY and (u == "m3" or _GAS.search(text)):
                return C.ENERGY_TAX_GAS, conf
            if category in (C.ELECTRICITY_LOW, C.ELECTRICITY_NORMAL, C.ELECTRICITY_SINGLE) and u == "m3":
                return C.GAS_SUPPLY, 0.7
            return category, conf
    if u == "m3" or _GAS.search(text):
        return C.GAS_SUPPLY, 0.8 if u == "m3" else 0.6
    if u in ("kWh", "MWh") or _ELEC.search(text):
        return C.ELECTRICITY_SINGLE, 0.7 if u in ("kWh", "MWh") else 0.5
    return C.OTHER, 0.3
