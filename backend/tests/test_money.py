"""The money type's dialect behaviour.

Both dialects are exercised directly, because the Postgres branch would
otherwise never run in this environment and would rot silently.
"""

from decimal import Decimal

from sqlalchemy.dialects import postgresql, sqlite

from app.money import Money

PG = postgresql.dialect()
LITE = sqlite.dialect()


def test_sqlite_bind_stores_integer_cents():
    assert Money().process_bind_param(Decimal("12.34"), LITE) == 1234


def test_sqlite_bind_accepts_string_input():
    assert Money().process_bind_param("0.05", LITE) == 5


def test_postgres_bind_keeps_decimal():
    assert Money().process_bind_param(Decimal("12.34"), PG) == Decimal("12.34")


def test_sqlite_result_returns_decimal():
    assert Money().process_result_value(1234, LITE) == Decimal("12.34")


def test_postgres_result_returns_decimal():
    assert Money().process_result_value(Decimal("12.34"), PG) == Decimal("12.34")


def test_none_passes_through_both_directions():
    assert Money().process_bind_param(None, LITE) is None
    assert Money().process_result_value(None, LITE) is None
    assert Money().process_bind_param(None, PG) is None
    assert Money().process_result_value(None, PG) is None


def test_rounding_is_half_up_not_truncation():
    """Half a cent must round up, not vanish."""
    assert Money().process_bind_param(Decimal("0.005"), LITE) == 1
    assert Money().process_bind_param(Decimal("0.004"), LITE) == 0


def test_postgres_column_type_is_numeric_12_2():
    """docs/07 §3 claims Postgres stores NUMERIC(12,2). This is the only place
    that claim is checked."""
    assert str(Money().load_dialect_impl(PG)) == "NUMERIC(12, 2)"


def test_sqlite_column_type_is_integer():
    assert str(Money().load_dialect_impl(LITE)) == "INTEGER"
