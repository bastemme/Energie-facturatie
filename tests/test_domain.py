from datetime import date
from decimal import Decimal

import pytest

from app.domain.confidence import EvidenceQuality as Q
from app.domain.confidence import confidence_from_evidence
from app.domain.ean import ean_check_digit, is_valid_ean18
from app.domain.enums import Confidence
from app.domain.fees import calculate_success_fee
from app.domain.money import format_decimal_nl, format_eur, percentage_of, round_cents, to_decimal
from app.domain.periods import Period, months_between

D = Decimal


class TestMoney:
    def test_round_half_up(self):
        assert round_cents(D("2.345")) == D("2.35")
        assert round_cents(D("2.344")) == D("2.34")
        assert round_cents(D("-2.345")) == D("-2.35")

    def test_float_rejected(self):
        with pytest.raises(TypeError):
            to_decimal(0.1)

    def test_percentage(self):
        assert percentage_of(D("10000"), D("15")) == D("1500.00")
        assert percentage_of(D("333.33"), D("12.5")) == D("41.67")

    def test_format_eur(self):
        assert format_eur(D("1234567.891")) == "€ 1.234.567,89"
        assert format_eur(D("-12.5")) == "-€ 12,50"
        assert format_eur(D("0")) == "€ 0,00"
        assert format_eur(None) == "—"

    def test_format_decimal_nl(self):
        assert format_decimal_nl(D("0.12345")) == "0,12345"
        assert format_decimal_nl(D("14200")) == "14.200"
        assert format_decimal_nl(D("1234.5"), 2) == "1.234,50"


class TestPeriods:
    def test_days_inclusive(self):
        assert Period(date(2025, 1, 1), date(2025, 1, 31)).days == 31
        assert Period(date(2024, 2, 1), date(2024, 2, 29)).days == 29

    def test_invalid(self):
        with pytest.raises(ValueError):
            Period(date(2025, 2, 1), date(2025, 1, 1))

    def test_overlap_and_gap(self):
        a = Period(date(2025, 1, 1), date(2025, 1, 31))
        b = Period(date(2025, 1, 25), date(2025, 2, 28))
        assert a.overlap_days(b) == 7
        c = Period(date(2025, 2, 5), date(2025, 2, 28))
        assert a.gap_to(c) == Period(date(2025, 2, 1), date(2025, 2, 4))
        d = Period(date(2025, 2, 1), date(2025, 2, 28))
        assert a.gap_to(d) is None

    def test_months_between(self):
        assert months_between(Period(date(2025, 1, 1), date(2025, 3, 31))) == D(3)
        assert months_between(Period(date(2025, 1, 1), date(2025, 1, 31))) == D(1)
        half = months_between(Period(date(2025, 4, 1), date(2025, 4, 15)))
        assert half == D(15) / D(30)
        # cross-year
        assert months_between(Period(date(2024, 12, 1), date(2025, 1, 31))) == D(2)

    def test_fraction_of(self):
        year = Period(date(2025, 1, 1), date(2025, 12, 31))
        jan = Period(date(2025, 1, 1), date(2025, 1, 31))
        assert jan.fraction_of(year) == D(31) / D(365)


class TestEan:
    def test_check_digit(self):
        body = "87160000000000001"
        full = body + str(ean_check_digit(body))
        assert is_valid_ean18(full)
        wrong = body + str((ean_check_digit(body) + 1) % 10)
        assert not is_valid_ean18(wrong)

    def test_format(self):
        assert not is_valid_ean18("12345")
        assert not is_valid_ean18(None)
        assert not is_valid_ean18("87160000000000000A")


class TestFees:
    def test_example_from_brief(self):
        fee = calculate_success_fee(D("10000"), D("15"))
        assert fee.success_fee == D("1500.00")
        assert fee.client_receives == D("8500.00")

    def test_nothing_recovered_no_fee(self):
        assert calculate_success_fee(None, D("20")).success_fee == D("0.00")

    @pytest.mark.parametrize("pct,expected", [("10", "870.00"), ("15", "1305.00"), ("20", "1740.00"), ("25", "2175.00")])
    def test_configurable_percentages(self, pct, expected):
        assert calculate_success_fee(D("8700"), D(pct)).success_fee == D(expected)

    def test_invalid(self):
        with pytest.raises(ValueError):
            calculate_success_fee(D("100"), D("101"))
        with pytest.raises(ValueError):
            calculate_success_fee(D("-1"), D("10"))


class TestConfidence:
    def test_explicit_is_high(self):
        assert confidence_from_evidence([Q.EXPLICIT, Q.EXPLICIT]) == Confidence.HIGH

    def test_weakest_link(self):
        assert confidence_from_evidence([Q.EXPLICIT, Q.DERIVED]) == Confidence.MEDIUM
        assert confidence_from_evidence([Q.EXPLICIT, Q.STATISTICAL]) == Confidence.LOW

    def test_extraction_confidence(self):
        assert confidence_from_evidence([Q.EXPLICIT], extraction_confidence=0.8) == Confidence.MEDIUM
        assert confidence_from_evidence([Q.EXPLICIT], extraction_confidence=0.5) == Confidence.LOW

    def test_cap(self):
        assert confidence_from_evidence([Q.EXPLICIT], cap=Confidence.MEDIUM) == Confidence.MEDIUM

    def test_no_evidence_is_low(self):
        assert confidence_from_evidence([]) == Confidence.LOW
