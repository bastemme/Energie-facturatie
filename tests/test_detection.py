"""Detection engine tests with synthetic data containing known errors.

Rates used in energy-tax/VAT reference tests are hypothetical test values, not real Dutch rates.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.devtools.synthetic import SynLine, render_invoice_pdf
from app.domain.enums import (
    Classification,
    Confidence,
    ExtractionMethod,
    InvoiceType,
    ReadingType,
    ReviewStatus,
)
from app.ingestion.pipeline import ingest_upload
from app.models import Anomaly
from app.services.analysis import run_analysis
from app.services.recovery import conservative_total
from tests.builders import C, D, RateKind, add_contract, add_invoice, add_rate, add_reading, line
from tests.factories import EAN_2, electricity_invoice

JAN = (date(2026, 1, 1), date(2026, 1, 31))
FEB = (date(2026, 2, 1), date(2026, 2, 28))
MAR = (date(2026, 3, 1), date(2026, 3, 31))
BASIC_PRICES = [
    (C.ELECTRICITY_NORMAL, "kWh", "0.20", date(2025, 1, 1), None),
    (C.FIXED_SUPPLY_FEE, "month", "7.50", date(2025, 1, 1), None),
]


def findings(db, client, rule=None):
    run_analysis(db, client)
    q = select(Anomaly).where(Anomaly.client_id == client.id, Anomaly.is_stale.is_(False))
    if rule:
        q = q.where(Anomaly.rule_id == rule)
    return db.scalars(q).all()


@pytest.fixture
def client(client_factory):
    return client_factory()


# ---------------------------------------------------------------- the example from the brief


def test_brief_example_tariff_error_detects_50_euro(db, client):
    """1000 kWh, contract €0.20/kWh, invoiced €0.25/kWh → expected €200, actual €250 → €50."""
    add_contract(db, client, BASIC_PRICES)
    add_invoice(db, client, "2026-0012", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.25")])
    [f] = findings(db, client, "contract_price")
    assert f.actual_value == D("250.00")
    assert f.expected_value == D("200.00")
    assert f.difference == D("50.00")
    assert f.potential_recovery == D("50.00")
    assert f.classification == Classification.POTENTIAL_ERROR
    assert f.confidence == Confidence.HIGH  # structured source + verified contract
    assert f.review_status == ReviewStatus.OPEN and f.requires_verification
    assert "Mogelijke discrepantie" in f.description and "2026-0012" in f.description
    kinds = {e["kind"] for e in f.evidence}
    assert kinds == {"invoice_line", "contract_price"}


def test_brief_example_end_to_end_from_pdf(db, client):
    add_contract(db, client, BASIC_PRICES)
    syn = electricity_invoice("2026-0012", normal_qty=D("1000"), normal_price=D("0.25"), low_qty=D("0"))
    syn.lines = [syn.lines[0], syn.lines[2]]
    doc = ingest_upload(db, client, "f.pdf", render_invoice_pdf(syn), None)
    [f] = findings(db, client, "contract_price")
    assert f.potential_recovery == D("50.00")
    # unverified PDF extraction → not HIGH until a reviewer verifies the invoice
    assert f.confidence == Confidence.MEDIUM
    ev = next(e for e in f.evidence if e["kind"] == "invoice_line")
    assert ev["document_id"] == doc.id and ev["page"] == 1 and ev["bbox"]


def test_verified_invoice_upgrades_confidence(db, client):
    add_contract(db, client, BASIC_PRICES)
    inv = add_invoice(db, client, "X", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.25")],
                      method=ExtractionMethod.REGEX)
    [f] = findings(db, client, "contract_price")
    assert f.confidence == Confidence.MEDIUM
    from datetime import UTC, datetime
    inv.verified_at = datetime.now(UTC)
    [f] = findings(db, client, "contract_price")
    assert f.confidence == Confidence.HIGH


def test_unverified_contract_is_medium(db, client):
    add_contract(db, client, BASIC_PRICES, verified=False)
    add_invoice(db, client, "X", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.25")])
    [f] = findings(db, client, "contract_price")
    assert f.confidence == Confidence.MEDIUM
    assert "nog niet geverifieerd" in f.description


def test_correct_invoice_produces_no_price_findings(db, client):
    add_contract(db, client, BASIC_PRICES)
    add_invoice(db, client, "OK", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20"),
                                         line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")])
    fs = findings(db, client)
    assert [f for f in fs if f.classification == Classification.POTENTIAL_ERROR] == []


def test_undercharge_recorded_without_recovery(db, client):
    add_contract(db, client, BASIC_PRICES)
    add_invoice(db, client, "U", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.18")])
    [f] = findings(db, client, "contract_price")
    assert f.difference == D("-20.00") and f.potential_recovery == D("0")


# ---------------------------------------------------------------- contract details


def test_price_change_mid_period_is_prorated(db, client):
    add_contract(db, client, [
        (C.ELECTRICITY_NORMAL, "kWh", "0.20", date(2025, 1, 1), date(2026, 1, 15)),
        (C.ELECTRICITY_NORMAL, "kWh", "0.22", date(2026, 1, 16), None),
    ])
    add_invoice(db, client, "P", *JAN, [line(C.ELECTRICITY_NORMAL, 3100, "kWh", "0.22")])
    [f] = findings(db, client, "contract_price")
    # 3100 × 15/31 × 0.20 = 300.00 ; 3100 × 16/31 × 0.22 = 352.00
    assert f.expected_value == D("652.00")
    assert f.potential_recovery == D("30.00")
    assert f.confidence == Confidence.MEDIUM  # prorating assumes uniform consumption


def test_mwh_contract_price_converted(db, client):
    add_contract(db, client, [(C.ELECTRICITY_NORMAL, "MWh", "200", date(2025, 1, 1), None)])
    add_invoice(db, client, "M", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.25")])
    [f] = findings(db, client, "contract_price")
    assert f.expected_value == D("200.00") and f.potential_recovery == D("50.00")


def test_fixed_fee_above_contract(db, client):
    add_contract(db, client, BASIC_PRICES)
    add_invoice(db, client, "F", *JAN, [line(C.FIXED_SUPPLY_FEE, 1, "month", "9.50")])
    [f] = findings(db, client, "contract_price")
    assert f.potential_recovery == D("2.00") and f.category == "fixed_charge"


def test_fixed_fee_without_quantity_prorated_by_months(db, client):
    add_contract(db, client, BASIC_PRICES)
    add_invoice(db, client, "Q1", date(2026, 1, 1), date(2026, 3, 31),
                [line(C.FIXED_SUPPLY_FEE, None, None, None, amount="30.00", desc="Vaste leveringskosten")])
    [f] = findings(db, client, "contract_price")
    assert f.expected_value == D("22.50") and f.potential_recovery == D("7.50")


def test_other_supplier_contract_not_applied(db, client):
    add_contract(db, client, BASIC_PRICES, supplier="Andere Leverancier")
    add_invoice(db, client, "S", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.25")])
    assert findings(db, client, "contract_price") == []


def test_feed_in_compensation_too_low(db, client):
    add_contract(db, client, [(C.ELECTRICITY_FEED_IN, "kWh", "0.10", date(2025, 1, 1), None)])
    add_invoice(db, client, "T", *JAN, [line(C.ELECTRICITY_FEED_IN, 1000, "kWh", "-0.08")])
    [f] = findings(db, client, "contract_price")
    assert f.actual_value == D("-80.00") and f.expected_value == D("-100.00")
    assert f.potential_recovery == D("20.00")


def test_invoice_outside_contract_period(db, client):
    add_contract(db, client, BASIC_PRICES, start=date(2025, 1, 1), end=date(2026, 1, 15))
    add_invoice(db, client, "O", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20")])
    [f] = findings(db, client, "contract_period")
    assert f.classification == Classification.ANOMALY and "16 dagen" in f.description


def test_fixed_charge_billed_for_too_many_months(db, client):
    add_invoice(db, client, "FM", *JAN, [line(C.FIXED_SUPPLY_FEE, 2, "month", "7.50")])
    [f] = findings(db, client, "fixed_charge_period")
    assert f.potential_recovery == D("7.50")


# ---------------------------------------------------------------- arithmetic & totals


def test_line_arithmetic_error(db, client):
    add_invoice(db, client, "A", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20", amount="250.00")])
    [f] = findings(db, client, "line_arithmetic")
    assert f.expected_value == D("200.00") and f.potential_recovery == D("50.00")
    assert f.confidence == Confidence.HIGH


def test_display_rounded_tariff_is_not_an_error(db, client):
    # Tariff printed as €0,10 but calculated with €0,10154: 2234 × 0.10154 = 226.84
    add_invoice(db, client, "R", *JAN, [line(C.ENERGY_TAX_ELECTRICITY, 2234, "kWh", "0.10", amount="226.84")])
    assert findings(db, client, "line_arithmetic") == []


def test_rounding_within_tolerance(db, client):
    add_invoice(db, client, "R2", *JAN, [line(C.ELECTRICITY_NORMAL, "1234.5", "kWh", "0.123456", amount="152.42")])
    assert findings(db, client, "line_arithmetic") == []


def test_below_materiality_dropped(db, client):
    add_invoice(db, client, "Z", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20", amount="200.30")])
    assert findings(db, client, "line_arithmetic") == []


def test_subtotal_and_total_mismatch(db, client):
    add_invoice(db, client, "T1", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20")],
                subtotal=D("210.00"), vat_amount=D("44.10"), total=D("260.00"))
    fs = {f.title.split(" (")[0]: f for f in findings(db, client, "invoice_totals")}
    sub = fs["Subtotaal wijkt af van de som van de regels"]
    assert sub.potential_recovery == D("10.00") and sub.confidence == Confidence.HIGH
    tot = fs["Totaalbedrag ≠ subtotaal + btw"]
    assert tot.difference == D("5.90")


def test_subtotal_mismatch_on_unverified_pdf_is_low(db, client):
    add_invoice(db, client, "T2", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20")],
                subtotal=D("250.00"), method=ExtractionMethod.REGEX, extraction_confidence=0.7)
    [f] = findings(db, client, "invoice_totals")
    assert f.confidence == Confidence.LOW and "automatisch uitgelezen" in f.description


# ---------------------------------------------------------------- VAT


def test_vat_amount_wrong_for_vat_deductible_client(db, client):
    add_invoice(db, client, "V", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20")], vat_amount=D("50.00"))
    [f] = findings(db, client, "vat")
    assert f.expected_value == D("42.00") and f.difference == D("8.00")
    assert f.potential_recovery == D("0")  # no net effect when VAT is deducted
    assert f.confidence == Confidence.MEDIUM and f.requires_verification


def test_vat_amount_wrong_non_deductible(db, client):
    client.vat_deductible = False
    add_invoice(db, client, "V2", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20")], vat_amount=D("50.00"))
    [f] = findings(db, client, "vat")
    assert f.potential_recovery == D("8.00")


def test_vat_rate_vs_verified_reference_only(db, client):
    client.vat_deductible = False
    add_rate(db, RateKind.VAT, "9", "percent", date(2020, 1, 1), verified=False)
    add_invoice(db, client, "V3", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20")])
    assert findings(db, client, "vat") == []  # unverified reference ignored
    add_rate(db, RateKind.VAT, "9", "percent", date(2020, 1, 1), verified=True)
    [f] = findings(db, client, "vat")
    assert f.expected_value == D("18.00") and f.potential_recovery == D("24.00")
    assert any(e["kind"] == "reference_rate" for e in f.evidence)


# ---------------------------------------------------------------- periods & duplicates


def test_overlapping_periods(db, client):
    add_invoice(db, client, "P1", *JAN, [line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")])
    add_invoice(db, client, "P2", date(2026, 1, 20), date(2026, 2, 28),
                [line(C.FIXED_SUPPLY_FEE, 1, "month", "10.00", amount="10.00")])
    [f] = findings(db, client, "billing_period_overlap")
    assert f.actual_value == D("12")  # 20–31 Jan
    assert f.potential_recovery == D("3.00")  # 10.00 × 12/40


def test_supplier_switch_double_billing(db, client):
    add_invoice(db, client, "A1", *JAN, [line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")], supplier="Eneco")
    add_invoice(db, client, "B1", *JAN, [line(C.FIXED_SUPPLY_FEE, 1, "month", "6.00")], supplier="Vattenfall")
    [f] = findings(db, client, "billing_period_overlap")
    assert "twee verschillende leveranciers" in f.description


def test_network_operator_invoice_not_overlap(db, client):
    add_invoice(db, client, "A1", *JAN, [line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")], supplier="Eneco")
    add_invoice(db, client, "N1", *JAN, [line(C.NETWORK_FIXED, 1, "month", "20")], supplier="Liander N.V.")
    assert findings(db, client, "billing_period_overlap") == []


def test_gap_is_anomaly_only(db, client):
    add_invoice(db, client, "G1", *JAN, [line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")])
    add_invoice(db, client, "G3", *MAR, [line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")])
    [f] = findings(db, client, "billing_period_gap")
    assert f.classification == Classification.ANOMALY and f.potential_recovery == D("0")
    assert "28 dagen" in f.title


def test_same_invoice_uploaded_twice_counted_once(db, client):
    add_invoice(db, client, "DUP", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20", amount="250")])
    add_invoice(db, client, "DUP", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20", amount="250")],
                method=ExtractionMethod.REGEX)
    fs = findings(db, client)
    assert len([f for f in fs if f.rule_id == "line_arithmetic"]) == 1
    [d] = [f for f in fs if f.rule_id == "duplicate_invoice_number"]
    assert d.classification == Classification.ANOMALY and d.potential_recovery == 0
    assert not [f for f in fs if f.rule_id == "billing_period_overlap"]


def test_billed_twice_under_different_numbers(db, client):
    add_invoice(db, client, "N-1", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20")],
                invoice_date=date(2026, 2, 1))
    add_invoice(db, client, "N-2", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20")],
                invoice_date=date(2026, 2, 10))
    [f] = findings(db, client, "duplicate_invoice_content")
    assert f.potential_recovery == D("200.00") and "N-2" in f.title
    assert not findings(db, client, "billing_period_overlap")


def test_duplicate_line(db, client):
    add_invoice(db, client, "DL", *JAN, [line(C.METER_RENTAL, 1, "month", "25"), line(C.METER_RENTAL, 1, "month", "25")])
    [f] = findings(db, client, "duplicate_line")
    assert f.potential_recovery == D("25.00")


# ---------------------------------------------------------------- consumption & meters


def test_invoice_consumption_vs_readings_on_invoice(db, client):
    """Invoice charges 14,200 kWh; readings indicate 9,800 kWh → 4,400 kWh excess."""
    inv = add_invoice(db, client, "2026-0012", *JAN, [line(C.ELECTRICITY_NORMAL, 14200, "kWh", "0.20")])
    add_reading(db, client, JAN[0], 50000, invoice=inv)
    add_reading(db, client, JAN[1], 59800, invoice=inv)
    [f] = findings(db, client, "meter_consumption")
    assert f.actual_value == D("14200") and f.expected_value == D("9800") and f.difference == D("4400")
    assert f.potential_recovery == D("880.00")
    assert "14.200 kWh" in f.description and "9.800 kWh" in f.description


def test_external_meter_data_with_multiplier(db, client):
    add_invoice(db, client, "MD", *JAN, [line(C.ELECTRICITY_NORMAL, 5000, "kWh", "0.20")])
    add_reading(db, client, JAN[0], 1000, multiplier=40)
    add_reading(db, client, JAN[1], 1100, multiplier=40)  # 100 × 40 = 4000 kWh
    [f] = findings(db, client, "meter_consumption")
    assert f.expected_value == D("4000") and f.potential_recovery == D("200.00")
    assert "vermenigvuldigingsfactor 40" in f.description


def test_estimated_reading_lowers_confidence_and_flags(db, client):
    inv = add_invoice(db, client, "E", *JAN, [line(C.ELECTRICITY_NORMAL, 1500, "kWh", "0.20")])
    add_reading(db, client, JAN[0], 1000, invoice=inv)
    add_reading(db, client, JAN[1], 2000, invoice=inv, rtype=ReadingType.ESTIMATED)
    [f] = findings(db, client, "meter_consumption")
    assert f.confidence == Confidence.LOW
    [e] = findings(db, client, "estimated_reading")
    assert e.classification == Classification.ANOMALY


def test_negative_consumption(db, client):
    inv = add_invoice(db, client, "NEG", *JAN, [line(C.ELECTRICITY_NORMAL, 100, "kWh", "0.20")])
    add_reading(db, client, JAN[0], 2000, invoice=inv)
    add_reading(db, client, JAN[1], 1900, invoice=inv)
    [f] = findings(db, client, "meter_consumption")
    assert f.classification == Classification.ANOMALY


def test_reading_continuity_double_billed_volume(db, client):
    a = add_invoice(db, client, "C1", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20")])
    add_reading(db, client, JAN[0], 1000, invoice=a)
    add_reading(db, client, JAN[1], 2000, invoice=a)
    b = add_invoice(db, client, "C2", *FEB, [line(C.ELECTRICITY_NORMAL, 1100, "kWh", "0.20")])
    add_reading(db, client, FEB[0], 1900, invoice=b)
    add_reading(db, client, FEB[1], 3000, invoice=b)
    [f] = findings(db, client, "meter_continuity")
    assert f.potential_recovery == D("20.00")  # 100 kWh × 0.20


def test_consumption_spike_is_anomaly(db, client):
    for i, month in enumerate([1, 2, 3, 4]):
        start = date(2026, month, 1)
        end = date(2026, month, 28)
        qty = 10000 if month == 4 else 2800
        add_invoice(db, client, f"H{i}", start, end, [line(C.ELECTRICITY_NORMAL, qty, "kWh", "0.20")])
    fs = findings(db, client, "consumption_trend")
    assert len(fs) == 1
    assert fs[0].classification == Classification.ANOMALY and fs[0].confidence == Confidence.LOW
    assert fs[0].potential_recovery == 0 and "geen bewijs" in fs[0].description


def test_no_trend_without_history(db, client):
    add_invoice(db, client, "H", *JAN, [line(C.ELECTRICITY_NORMAL, 99999, "kWh", "0.20")])
    assert findings(db, client, "consumption_trend") == []


# ---------------------------------------------------------------- credits


def test_partial_credit_with_rebill(db, client):
    add_invoice(db, client, "ORIG", *JAN, [line(C.ELECTRICITY_NORMAL, 5000, "kWh", "0.20")],
                invoice_date=date(2026, 2, 1))  # 1000.00
    add_invoice(db, client, "CR", *JAN, [line(C.CORRECTION, None, None, None, amount="-800.00")],
                invoice_type=InvoiceType.CREDIT_NOTE, corrects="ORIG", invoice_date=date(2026, 3, 1))
    add_invoice(db, client, "REB", *JAN, [line(C.ELECTRICITY_NORMAL, 4500, "kWh", "0.20")],
                invoice_date=date(2026, 3, 1))
    [f] = findings(db, client, "credit_reconciliation")
    assert f.potential_recovery == D("200.00")
    # the credited original is excluded from overlap checks
    assert findings(db, client, "billing_period_overlap") == []


def test_credit_note_missing_original(db, client):
    add_invoice(db, client, "CR", *JAN, [line(C.CORRECTION, None, None, None, amount="-50")],
                invoice_type=InvoiceType.CREDIT_NOTE, corrects="NOPE")
    [f] = findings(db, client, "credit_reconciliation")
    assert f.classification == Classification.ANOMALY and "ontbreekt" in f.title


def test_findings_on_credited_invoice_are_annotated(db, client):
    add_invoice(db, client, "ORIG", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20", amount="250")])
    add_invoice(db, client, "CR", *JAN, [line(C.CORRECTION, None, None, None, amount="-50")],
                invoice_type=InvoiceType.CREDIT_NOTE, corrects="ORIG")
    [f] = findings(db, client, "line_arithmetic")
    assert "gecorrigeerd met creditnota CR" in f.description


def test_credit_note_with_positive_amounts_sign(db, client):
    # credit printed with positive numbers: arithmetic error that credits too little is an overcharge
    add_invoice(db, client, "CRP", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20", amount="150")],
                invoice_type=InvoiceType.CREDIT_NOTE, corrects="X")
    [f] = findings(db, client, "line_arithmetic")
    assert f.difference == D("-50.00") and f.potential_recovery == D("50.00")


# ---------------------------------------------------------------- energy tax (hypothetical test rates)


def _test_brackets(db, verified=True):
    add_rate(db, RateKind.ENERGY_TAX_ELECTRICITY, "0.10", "kWh", date(2026, 1, 1), bracket_from=0,
             bracket_to=10000, verified=verified)
    add_rate(db, RateKind.ENERGY_TAX_ELECTRICITY, "0.05", "kWh", date(2026, 1, 1), bracket_from=10000,
             bracket_to=None, verified=verified)


def test_energy_tax_bracket_error(db, client):
    _test_brackets(db)
    year = (date(2026, 1, 1), date(2026, 12, 31))
    add_invoice(db, client, "EB", *year, [line(C.ELECTRICITY_NORMAL, 20000, "kWh", "0.20"),
                                          line(C.ENERGY_TAX_ELECTRICITY, 20000, "kWh", "0.10")])
    [f] = findings(db, client, "energy_tax")
    assert f.expected_value == D("1500.00") and f.potential_recovery == D("500.00")
    assert f.confidence == Confidence.MEDIUM and f.requires_verification
    assert "tijdsevenredig" in f.description


def test_energy_tax_skipped_without_verified_rates(db, client):
    _test_brackets(db, verified=False)
    add_invoice(db, client, "EB", date(2026, 1, 1), date(2026, 12, 31),
                [line(C.ENERGY_TAX_ELECTRICITY, 20000, "kWh", "0.10")])
    assert findings(db, client, "energy_tax") == []


def test_missing_tax_reduction_anomaly(db, client):
    add_invoice(db, client, "TR", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20"),
                                         line(C.ENERGY_TAX_ELECTRICITY, 1000, "kWh", "0.10")])
    [f] = findings(db, client, "energy_tax_reduction_missing")
    assert f.classification == Classification.ANOMALY and f.potential_recovery == 0


# ---------------------------------------------------------------- engine behaviour


def test_rerun_is_idempotent_and_preserves_review(db, client):
    add_invoice(db, client, "A", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20", amount="250.00")])
    [f] = findings(db, client, "line_arithmetic")
    f.review_status = ReviewStatus.CONFIRMED
    f.review_notes = "Gecontroleerd"
    db.flush()
    [again] = findings(db, client, "line_arithmetic")
    assert again.id == f.id and again.review_status == ReviewStatus.CONFIRMED


def test_stale_when_data_corrected(db, client):
    inv = add_invoice(db, client, "A", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20", amount="250.00")])
    [f] = findings(db, client, "line_arithmetic")
    inv.lines[0].amount = D("200.00")
    db.flush()
    run_analysis(db, client)
    db.refresh(f)
    assert f.is_stale


def test_conservative_total_does_not_double_count(db, client):
    add_contract(db, client, BASIC_PRICES)
    add_invoice(db, client, "DC", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.20", amount="250.00")])
    fs = findings(db, client)
    per_rule = {f.rule_id: f.potential_recovery for f in fs if f.potential_recovery > 0}
    assert per_rule["line_arithmetic"] == D("50.00") and per_rule["contract_price"] == D("50.00")
    assert conservative_total(fs) == D("50.00")


def test_tenant_isolation_in_analysis(db, client_factory):
    a, b = client_factory("A"), client_factory("B")
    add_contract(db, a, BASIC_PRICES)
    add_invoice(db, b, "B1", *JAN, [line(C.ELECTRICITY_NORMAL, 1000, "kWh", "0.25")])
    assert findings(db, b, "contract_price") == []  # a's contract must never apply to b


def test_detector_failure_is_recorded_not_silent(db, client, monkeypatch):
    from app.detection import registry
    from app.detection.base import Detector

    def boom(ctx):
        raise RuntimeError("x")

    monkeypatch.setattr(registry, "ALL_DETECTORS", [Detector("boom", "1", "", boom)])
    run = run_analysis(db, client)
    assert run.errors == [{"rule_id": "boom", "error": "RuntimeError"}]


def test_pdf_meter_readings_flow(db, client):
    syn = electricity_invoice("RD", normal_qty=D("14200"), low_qty=D("0"),
                              readings=[("begin", "normaal", date(2026, 1, 1), D("50000"), "werkelijk"),
                                        ("eind", "normaal", date(2026, 1, 31), D("59800"), "werkelijk")])
    syn.lines = [syn.lines[0], syn.lines[2], SynLine("Meterhuur", D("1"), "maand", D("2.50"))]
    ingest_upload(db, client, "rd.pdf", render_invoice_pdf(syn), None)
    [f] = findings(db, client, "meter_consumption")
    assert f.difference == D("4400") and f.potential_recovery == D("880.00")


def test_multiple_eans_do_not_mix(db, client):
    add_invoice(db, client, "E1", *JAN, [line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")])
    add_invoice(db, client, "E2", *JAN, [line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")], ean=EAN_2)
    assert findings(db, client, "billing_period_overlap") == []


def test_fixed_charge_anniversary_period_not_flagged(db, client):
    add_invoice(db, client, "AN", date(2026, 1, 15), date(2026, 2, 14), [line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")])
    add_invoice(db, client, "FEB", date(2026, 2, 1), date(2026, 2, 28), [line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")],
                ean=EAN_2)
    assert findings(db, client, "fixed_charge_period") == []


def test_one_day_boundary_differences_ignored(db, client):
    # supplier uses exclusive end dates: 01-01 – 01-02, then 01-02 – 01-03 (1-day "overlap")
    add_invoice(db, client, "X1", date(2026, 1, 1), date(2026, 2, 1), [line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")])
    add_invoice(db, client, "X2", date(2026, 2, 1), date(2026, 3, 1), [line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")])
    add_invoice(db, client, "X3", date(2026, 3, 3), date(2026, 3, 31), [line(C.FIXED_SUPPLY_FEE, 1, "month", "7.50")])
    assert findings(db, client, "billing_period_overlap") == []
    assert findings(db, client, "billing_period_gap") == []  # 1-day gap (02-03) tolerated
