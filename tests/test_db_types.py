from decimal import Decimal

import pytest

from app.models import Client


def test_decimal_exact_roundtrip(db):
    c = Client(company_name="X", success_fee_percentage=Decimal("15.1234"),
               approx_annual_energy_spend=Decimal("12345678.1234"))
    db.add(c)
    db.commit()
    db.expire_all()
    loaded = db.get(Client, c.id)
    assert loaded.success_fee_percentage == Decimal("15.1234")
    assert loaded.approx_annual_energy_spend == Decimal("12345678.1234")
    assert isinstance(loaded.approx_annual_energy_spend, Decimal)


def test_float_rejected(db):
    db.add(Client(company_name="X", success_fee_percentage=0.15))
    with pytest.raises(Exception):
        db.commit()
