"""EAN-18 connection codes (aansluitingen). Dutch EAN codes start with 871."""

import re

_EAN_RE = re.compile(r"^\d{18}$")


def ean_check_digit(first17: str) -> int:
    """GS1 mod-10 check digit: weights 3,1,3,1,... starting from the rightmost data digit."""
    total = 0
    for i, ch in enumerate(reversed(first17)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


def is_valid_ean18(value: str | None) -> bool:
    if not value or not _EAN_RE.match(value):
        return False
    return ean_check_digit(value[:17]) == int(value[17])


def normalize_ean(value: str | None) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    return digits or None
