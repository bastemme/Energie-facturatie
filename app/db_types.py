"""Exact decimal storage on every backend.

SQLite has no exact NUMERIC type (SQLAlchemy would round-trip through float), so on SQLite
decimals are stored as canonical strings. PostgreSQL uses NUMERIC.
"""

from decimal import Decimal

from sqlalchemy import Numeric, String
from sqlalchemy.types import TypeDecorator


class ExactDecimal(TypeDecorator):
    impl = String(64)
    cache_ok = True

    def __init__(self, precision: int = 18, scale: int = 6):
        super().__init__()
        self.precision = precision
        self.scale = scale

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError("float values are not allowed for ExactDecimal columns")
        value = Decimal(value)
        if dialect.name == "sqlite":
            return format(value, "f")
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value) if not isinstance(value, Decimal) else value


Money = ExactDecimal(18, 4)
UnitPrice = ExactDecimal(18, 8)
Quantity = ExactDecimal(18, 4)
Percentage = ExactDecimal(7, 4)
