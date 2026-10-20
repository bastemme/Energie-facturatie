"""Maskeren van klantgegevens in inspectie-uitvoer.

De inspectietools draaien op de computer van de gebruiker, maar de uitvoer wordt
gedeeld om de automatisering te ontwerpen. Daarom gaan er geen klantnummers,
EAN-codes, namen, adressen of meetwaarden in de uitvoer: alleen de structuur.
"""

from __future__ import annotations

import re

# Elementtypen waarvan de naam bijna altijd een vast label van de interface is
# (knoppen, menu's, tabbladen, kolomkoppen). Reeksen van 4+ cijfers worden ook hier gemaskeerd.
STRUCTURELE_TYPEN = {
    "Button", "SplitButton", "MenuItem", "Menu", "MenuBar", "TabItem", "Tab", "Header", "HeaderItem",
    "CheckBox", "RadioButton", "Group", "TitleBar", "ToolBar", "ScrollBar", "Thumb", "Hyperlink", "Window",
}

# Elementtypen die gegevens bevatten (lijsten, tabellen); tekst daarbinnen is data, geen label.
DATA_CONTAINERS = {"List", "DataGrid", "Table", "Tree"}

_CIJFERREEKS = re.compile(r"\d{4,}")


def maskeer_cijferreeksen(tekst: str) -> str:
    """Vervang elke reeks van 4 of meer cijfers door alleen de lengte, bv. '<18 cijfers>'."""
    return _CIJFERREEKS.sub(lambda m: f"<{len(m.group())} cijfers>", tekst)


def vorm(tekst: str) -> str:
    """Beschrijf de vorm van een waarde zonder de waarde zelf: cijfers -> 9, lettergroepen -> A.

    Het aantal cijfers blijft zichtbaar (belangrijk voor EAN-lengte en voorloopnullen),
    letters niet: 'Kerkstraat 12' wordt 'A 99', '2026-10-01 00:15' wordt '9999-99-99 99:99'.
    """
    tekst = re.sub(r"\d", "9", tekst)
    return re.sub(r"[^\W\d_]+", "A", tekst)


def veilige_naam(control_type: str, naam: str, in_data_container: bool, maskeren: bool = True) -> str:
    """Geef de naam van een UI-element terug zoals die in de inspectie-uitvoer mag staan."""
    if not maskeren or not naam:
        return naam
    if control_type in STRUCTURELE_TYPEN:
        return maskeer_cijferreeksen(naam)
    if control_type == "Text" and not in_data_container and not re.search(r"\d", naam):
        # Losse tekst buiten een lijst zonder cijfers is meestal een veldlabel ('Zoeken', 'Formaat').
        # Restrisico: een klantnaam in een detailpaneel. Controleer de uitvoer daarom voor het delen.
        return naam
    return f"<verborgen: {len(naam)} tekens, vorm {vorm(naam)!r}>"
