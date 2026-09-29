from datetime import date
from decimal import Decimal

import pytest

from app.domain.enums import LineCategory as C
from app.domain.units import convert_quantity, convert_unit_price, normalize_unit
from app.extraction.categories import classify_line
from app.extraction.classify import classify_text
from app.extraction.parsing import parse_date, parse_decimal

D = Decimal


@pytest.mark.parametrize("text,expected", [
    ("1.234,56", "1234.56"), ("-12,00", "-12.00"), ("12,00-", "-12.00"), ("€ 1.234", "1234"),
    ("0,12345", "0.12345"), ("1234.56", "1234.56"), ("1,234.56", "1234.56"), ("€ -52,06", "-52.06"),
    ("-€ 52,06", "-52.06"), ("(10,00)", "-10.00"), ("1.234.567", "1234567"), ("14200", "14200"),
    ("1 234,50", "1234.50"),
])
def test_parse_decimal(text, expected):
    assert parse_decimal(text).value == D(expected)


def test_parse_decimal_ambiguous_flag():
    p = parse_decimal("1.234")
    assert p.value == D("1234") and p.ambiguous
    assert parse_decimal("1.234", locale="en").value == D("1.234")
    assert not parse_decimal("0.25").ambiguous


@pytest.mark.parametrize("text", ["", "abc", "12,3,4", "€", None, "1.23.4"])
def test_parse_decimal_rejects(text):
    assert parse_decimal(text) is None


@pytest.mark.parametrize("text,expected", [
    ("31-01-2025", date(2025, 1, 31)), ("1-2-2025", date(2025, 2, 1)), ("2025-03-04", date(2025, 3, 4)),
    ("05/06/2025", date(2025, 6, 5)), ("1 januari 2025", date(2025, 1, 1)), ("15 okt. 2025", date(2025, 10, 15)),
    ("31.12.24", date(2024, 12, 31)),
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_parse_date_invalid():
    assert parse_date("31-02-2025") is None
    assert parse_date("gisteren") is None


@pytest.mark.parametrize("desc,unit,expected", [
    ("Levering elektriciteit normaaltarief", "kWh", C.ELECTRICITY_NORMAL),
    ("Levering elektriciteit daltarief", "kWh", C.ELECTRICITY_LOW),
    ("Elektriciteit enkeltarief", "kWh", C.ELECTRICITY_SINGLE),
    ("Energiebelasting elektriciteit", "kWh", C.ENERGY_TAX_ELECTRICITY),
    ("Energiebelasting gas", "m3", C.ENERGY_TAX_GAS),
    ("Vermindering energiebelasting", None, C.TAX_REDUCTION),
    ("Levering gas", "m3", C.GAS_SUPPLY),
    ("Vaste leveringskosten", "maand", C.FIXED_SUPPLY_FEE),
    ("Meterhuur", "maand", C.METER_RENTAL),
    ("Capaciteitstarief netbeheerder", "maand", C.NETWORK_CAPACITY),
    ("Teruglevering", "kWh", C.ELECTRICITY_FEED_IN),
    ("Welkomstkorting", None, C.DISCOUNT),
    ("Iets onbekends", None, C.OTHER),
])
def test_classify_line(desc, unit, expected):
    assert classify_line(desc, unit)[0] == expected


def test_units():
    assert normalize_unit("m³") == "m3"
    assert normalize_unit("Maanden") == "month"
    assert convert_quantity(D("1.5"), "MWh", "kWh") == D("1500")
    assert convert_unit_price(D("250"), "MWh", "kWh") == D("0.25")
    assert convert_quantity(D("1"), "m3", "kWh") is None


def test_classify_text():
    assert classify_text("Factuurnummer 1 Factuurdatum 2 kWh levering")[0].value == "INVOICE"
    assert classify_text("Creditnota voor levering kWh")[0].value == "CREDIT_NOTE"
    assert classify_text("Leveringsovereenkomst contract looptijd algemene voorwaarden")[0].value == "CONTRACT"
