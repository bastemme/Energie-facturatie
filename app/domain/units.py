"""Unit normalization and conversion for quantities and tariffs."""

from decimal import Decimal

_ALIASES = {
    "kwh": "kWh",
    "mwh": "MWh",
    "m3": "m3",
    "m³": "m3",
    "nm3": "m3",
    "kw": "kW",
    "dag": "day",
    "dagen": "day",
    "day": "day",
    "days": "day",
    "maand": "month",
    "maanden": "month",
    "mnd": "month",
    "mnd.": "month",
    "month": "month",
    "jaar": "year",
    "jr": "year",
    "year": "year",
    "st": "piece",
    "stuk": "piece",
    "stuks": "piece",
    "eur": "EUR",
    "€": "EUR",
}

# factor to convert 1 <unit> into the base unit
_TO_BASE = {"kWh": ("kWh", Decimal(1)), "MWh": ("kWh", Decimal(1000))}


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    key = unit.strip().lower().rstrip(".")
    if not key:
        return None
    return _ALIASES.get(key, _ALIASES.get(key + ".", unit.strip()))


def base_unit(unit: str | None) -> tuple[str | None, Decimal]:
    """Return (base unit, factor) such that quantity_in_base = quantity × factor."""
    unit = normalize_unit(unit)
    if unit in _TO_BASE:
        return _TO_BASE[unit]
    return unit, Decimal(1)


def convert_quantity(quantity: Decimal, from_unit: str | None, to_unit: str | None) -> Decimal | None:
    fb, ff = base_unit(from_unit)
    tb, tf = base_unit(to_unit)
    if fb is None or fb != tb:
        return None
    return quantity * ff / tf


def convert_unit_price(price: Decimal, from_unit: str | None, to_unit: str | None) -> Decimal | None:
    """€/MWh → €/kWh etc. Price per unit converts inversely to quantity."""
    fb, ff = base_unit(from_unit)
    tb, tf = base_unit(to_unit)
    if fb is None or fb != tb:
        return None
    return price * tf / ff
