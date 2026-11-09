"""Log redaction.

docs/10 §4 promises that a log line cannot carry a credential. That promise was
undefended, so it is defended here.

Two honest limits, stated rather than implied:

1. This matches **key/value pairs**, not bare values. A secret logged with no
   key beside it (`print(secret)`) is not caught — no filter can catch that, and
   claiming otherwise would be the kind of assurance this project exists to
   avoid.
2. It rewrites the rendered message and drops `record.args`. A structured
   formatter that logs a dict keeps the keys and loses the values. A redacted
   line is worth more than a faithful one.

One pass, one pattern. An earlier version ran two substitutions and the first
one corrupted the input for the second (`authorization=[REDACTED] A21AA-…`),
which is exactly the kind of bug a filter cannot afford.
"""

from __future__ import annotations

import logging
import re

REDACTED = "[REDACTED]"

_SECRET_PAIR = re.compile(
    r"(client_secret|secret|token|authorization|password|api[_-]?key)"
    r"[\"']?\s*[:=]\s*[\"']?"
    # A scheme prefix (`Bearer xyz`) is part of the secret, not text to keep:
    # leaving it behind leaks the token while looking redacted.
    r"(?:(?:bearer|basic|token)\s+)?"
    r"[^\s,;\"'})]+",
    re.IGNORECASE,
)


class RedactSecretsFilter(logging.Filter):
    """Rewrites any `key=value` or `"key": "value"` whose key looks secret."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string must not lose the log
            return True

        redacted = _SECRET_PAIR.sub(r"\1=" + REDACTED, message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_logging() -> None:
    """Idempotent: called once per app factory, and by tests."""
    root = logging.getLogger()
    if not any(isinstance(f, RedactSecretsFilter) for f in root.filters):
        root.addFilter(RedactSecretsFilter())
    for handler in root.handlers:
        if not any(isinstance(f, RedactSecretsFilter) for f in handler.filters):
            handler.addFilter(RedactSecretsFilter())
