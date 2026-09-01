"""
Log redaction — R12, and INVOICE.md §12.8.

🔴 The values this module hides are the ones that make a log line useful to
somebody who should not have it: a GSTIN identifies a business, a bank account
number plus an IFSC is a payment instruction, and a phone number is personal
data under DPDP whether or not it belongs to a company director.

The design choice worth naming: this redacts by *pattern*, not by field name.
A field-name allow-list works right up until a GSTIN arrives inside an error
message, a provider's rejection reason, or the free-text body of a webhook —
which is exactly where it will arrive, because those are the paths nobody
sanitises.
"""

from __future__ import annotations

import logging
import re
from typing import Any

#: A full GSTIN or UIN. Keeps the state code and the last character, because
#: "the GSTIN starting 09 failed" is the diagnostic value, and the middle is
#: the identity.
_GSTIN = re.compile(r"\b([0-9]{2})([A-Z0-9]{11})([A-Z0-9]{2})\b")

#: Indian mobile numbers, with or without +91.
_PHONE = re.compile(r"\b(?:\+?91[\-\s]?)?([6-9]\d{9})\b")

_EMAIL = re.compile(r"\b([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")

#: Nine to eighteen digits together — a bank account. Deliberately broad: an
#: invoice total never runs to nine unbroken digits, and a false positive in a
#: log is cheap while a leaked account number is not.
_ACCOUNT = re.compile(r"\b\d{9,18}\b")

_PAN = re.compile(r"\b([A-Z]{5})([0-9]{4})([A-Z])\b")

#: Keys whose value is replaced wholesale rather than pattern-matched. These
#: are secrets, not identifiers — no part of them has diagnostic value.
SECRET_KEYS = frozenset(
    {
        "authorization",
        "password",
        "token",
        "access",
        "refresh",
        "secret",
        "api_key",
        "apikey",
        "webhook_secret",
        "signature",
        "x-api-key",
        "anthropic_api_key",
        "nvidia_api_key",
        "scrapfly_api_key",
    }
)


def redact_text(value: str) -> str:
    """Mask identifiers in a free-text string."""
    value = _GSTIN.sub(lambda m: f"{m.group(1)}***{m.group(3)}", value)
    value = _PAN.sub(lambda m: f"{m.group(1)[:2]}***{m.group(3)}", value)
    value = _PHONE.sub(lambda m: f"+91*****{m.group(1)[-4:]}", value)
    value = _EMAIL.sub(lambda m: f"{m.group(1)[:2]}***@{m.group(2)}", value)
    value = _ACCOUNT.sub(lambda m: f"****{m.group(0)[-4:]}", value)
    return value


def redact(value: Any, *, _depth: int = 0) -> Any:
    """
    Redact a structure recursively, for a log record's `extra` payload.

    Depth-limited. A cyclic or pathologically nested structure reaching a
    logger should degrade to a marker rather than recurse until the stack
    gives out — a logging call that raises takes down the request it was
    describing.
    """
    if _depth > 12:
        return "<nested>"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if str(key).lower() in SECRET_KEYS:
                out[key] = "<redacted>"
            else:
                out[key] = redact(item, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth=_depth + 1) for item in value]
    return value


class RedactingFilter(logging.Filter):
    """
    A logging filter that rewrites the formatted message.

    🔴 Attached to the root logger in `backend.main`, not to one named logger. A
    GSTIN leaks through whichever library happens to log the exception — httpx,
    SQLAlchemy, asyncpg — and none of those are ours to instrument.

    It works on `record.getMessage()` output rather than on `record.args`
    because that is the only place both the format string and its arguments
    have been combined; redacting the args alone misses a message that
    interpolated an identifier itself.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a broken format string is not this filter's problem
            return True

        redacted = redact_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install(logger: logging.Logger | None = None) -> None:
    """Attach the filter to a logger and to every handler it owns."""
    target = logger or logging.getLogger()
    log_filter = RedactingFilter()
    if not any(isinstance(existing, RedactingFilter) for existing in target.filters):
        target.addFilter(log_filter)
    for handler in target.handlers:
        if not any(isinstance(existing, RedactingFilter) for existing in handler.filters):
            handler.addFilter(log_filter)
