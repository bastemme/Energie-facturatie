"""Regression: the demo crashed on a second start when stored (naive) and new (aware) invoices were compared."""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.detection.duplicates import _preference


def test_preference_compares_naive_and_aware_created_at():
    old = SimpleNamespace(lines=[], is_verified=False, extraction_confidence=0.9, created_at=datetime(2026, 1, 1))
    new = SimpleNamespace(lines=[], is_verified=False, extraction_confidence=0.9,
                          created_at=datetime(2026, 2, 1, tzinfo=UTC))
    assert max([old, new], key=_preference) is new
