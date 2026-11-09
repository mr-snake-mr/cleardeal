"""docs/10 §4 promises a log line cannot carry a credential.

That promise had no test, and no implementation. Both now exist.
"""

import logging

from app.logging_config import REDACTED, RedactSecretsFilter

SECRET = "EL-SECRET-0123456789-abcdef"


def _record(message: str, *args) -> logging.LogRecord:
    return logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def _redact(message: str) -> str:
    record = _record(message)
    RedactSecretsFilter().filter(record)
    return record.getMessage()


def test_a_key_value_pair_is_redacted():
    assert SECRET not in _redact(f"connected merchant client_secret={SECRET}")


def test_a_json_style_pair_is_redacted():
    assert SECRET not in _redact(f'{{"client_secret": "{SECRET}"}}')


def test_a_bearer_token_is_redacted():
    assert "A21AA" not in _redact("headers: authorization=Bearer A21AA-secret-token")


def test_the_surrounding_text_survives():
    """A filter that erases the whole line makes logs useless, which is its own failure."""
    out = _redact(f"user=42 connecting client_secret={SECRET}")
    assert "user=42" in out
    assert "connecting" in out


def test_a_harmless_line_is_untouched():
    message = "GET /api/v1/projects 200 in 12ms"
    assert _redact(message) == message


def test_a_secret_value_with_no_key_is_not_claimed_to_be_caught():
    """Documents the limit instead of implying coverage the filter cannot give.

    `print(secret)` has no key to match on. Only review catches that, and the
    module docstring says so.
    """
    assert SECRET in _redact(f"value is {SECRET}")


def test_the_filter_is_installed_on_the_root_logger():
    from app.logging_config import install_logging

    install_logging()
    assert any(isinstance(f, RedactSecretsFilter) for f in logging.getLogger().filters)


def test_redaction_marker_is_stable():
    """A marker that changes shape breaks whatever greps for it."""
    assert REDACTED == "[REDACTED]"
