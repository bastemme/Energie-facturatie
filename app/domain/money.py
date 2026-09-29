"""Exact money arithmetic. Floats are never allowed in financial logic."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0")


def to_decimal(value: object) -> Decimal:
    """Convert int/str/Decimal to Decimal. Floats are rejected to avoid silent rounding errors."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("bool is not a monetary value")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal: {value!r}") from exc
    if isinstance(value, float):
        raise TypeError("float values are not allowed in financial calculations; use Decimal or str")
    raise TypeError(f"cannot convert {type(value).__name__} to Decimal")


def round_cents(value: Decimal) -> Decimal:
    """Commercial rounding to cents (half up), as used on Dutch invoices."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def percentage_of(amount: Decimal, percentage: Decimal) -> Decimal:
    """amount × percentage / 100, rounded to cents."""
    return round_cents(amount * percentage / Decimal(100))


def within_tolerance(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance


def format_eur(value: Decimal | None, *, signed: bool = False) -> str:
    """Dutch formatting: € 1.234,56. Returns '—' for missing values (never invents a number)."""
    if value is None:
        return "—"
    q = round_cents(value)
    sign = "-" if q < 0 else ("+" if signed and q > 0 else "")
    whole, frac = f"{abs(q):.2f}".split(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{sign}€ {'.'.join(groups)},{frac}"


def format_decimal_nl(value: Decimal | None, places: int | None = None) -> str:
    """Dutch decimal notation for quantities/tariffs, e.g. 1.234,5 or 0,12345."""
    if value is None:
        return "—"
    if places is not None:
        value = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = format(value.normalize() if places is None else value, "f")
    sign = "-" if text.startswith("-") else ""
    text = text.lstrip("-")
    whole, _, frac = text.partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    out = ".".join(groups)
    return f"{sign}{out},{frac}" if frac else f"{sign}{out}"
