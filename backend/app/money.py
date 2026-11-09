"""Exact money storage.

SQLite has no fixed-scale decimal type: SQLAlchemy's `Numeric` degrades to REAL
(floating point) and warns. Storing money that way is the failure docs/06 §5.1
forbids, and it makes the budget invariant compare imprecise floats.

`Money` keeps the logical type honest at both ends:
  - Postgres  -> NUMERIC(12,2)   (exact, native)
  - SQLite    -> INTEGER cents   (exact, integer arithmetic)

Callers always see and pass `Decimal`. Conversion happens only at the driver
boundary.
"""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Integer, Numeric
from sqlalchemy.types import TypeDecorator

TWO_PLACES = Decimal("0.01")


class Money(TypeDecorator):
    """A Decimal-valued column that never touches a float."""

    impl = Integer
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(12, 2))
        return dialect.type_descriptor(Integer())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
        if dialect.name == "postgresql":
            return amount.quantize(TWO_PLACES)
        # Rounding is explicit: silently truncating half a cent is how money
        # bugs start.
        return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return Decimal(value).quantize(TWO_PLACES)
        return (Decimal(int(value)) / 100).quantize(TWO_PLACES)
