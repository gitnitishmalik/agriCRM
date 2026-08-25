"""
Structured JSON logging with PII scrubbing.

🔴 R8 / R12: plaintext Aadhaar must never reach a log, and application logs are
retained for one year under DPDP Rule 6 — which means anything that lands here
is held for a year. Scrub at the handler, not at each call site: call sites get
forgotten, the handler does not.

This is a backstop, not a licence to log PII deliberately. Log identifiers
(farmer_id, org_id), never values.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

# 🔴 Order is load-bearing, and the reason is subtle.
#
# An Aadhaar is 12 digits starting 2-9. A mobile with a country code —
# 919876543210 — is also 12 digits starting with 9. They are genuinely
# ambiguous as bare digit strings, so the rules run most-specific first:
#
#   1. A phone with an explicit "+" country code. The "+" disambiguates.
#   2. Aadhaar, including the common 4-4-4 grouping.
#   3. A bare 10-digit mobile, optionally with a single leading 0.
#
# If rule 2 ran first it would consume "919876543210" out of "+919876543210"
# and leave a dangling "+", which still redacts the number but mislabels it.
# A bare 12-digit string with no "+" is redacted as Aadhaar — the label may be
# wrong but the value is gone either way, which is the property that matters.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 1. Explicit international form: +91 98765 43210
    (re.compile(r"\+91[ -]?[6-9]\d{9}\b"), "[PHONE_REDACTED]"),
    # 2. Aadhaar: 12 digits, optionally grouped 4-4-4 by space or hyphen
    (re.compile(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b"), "[AADHAAR_REDACTED]"),
    # 3. Bare Indian mobile, with or without a leading 0
    (re.compile(r"\b0?[6-9]\d{9}\b"), "[PHONE_REDACTED]"),
    # 4. Email
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL_REDACTED]"),
    # 5. PAN
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[PAN_REDACTED]"),
]

# Keys whose values are dropped wholesale regardless of shape.
_SENSITIVE_KEYS = frozenset(
    {
        "aadhaar",
        "aadhaar_number",
        "password",
        "token",
        "access",
        "refresh",
        "authorization",
        "secret",
        "api_key",
        "bank_account",
        "otp",
    }
)


def scrub(value: str) -> str:
    """Redact PII patterns from a string."""
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def scrub_mapping(data: dict) -> dict:
    """Recursively scrub a dict, dropping sensitive keys entirely."""
    out = {}
    for key, val in data.items():
        if key.lower() in _SENSITIVE_KEYS:
            out[key] = "[REDACTED]"
        elif isinstance(val, dict):
            out[key] = scrub_mapping(val)
        elif isinstance(val, str):
            out[key] = scrub(val)
        else:
            out[key] = val
    return out


class ScrubPIIFilter(logging.Filter):
    """Scrub the rendered message and any structured extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = scrub_mapping(record.args)
            else:
                record.args = tuple(scrub(a) if isinstance(a, str) else a for a in record.args)
        return True


class JSONFormatter(logging.Formatter):
    """
    One JSON object per line, with the correlation fields Doc 04 §9 requires:
    request_id, user_id, entity_type, entity_id — set via `extra=`.
    """

    _CORRELATION_FIELDS = ("request_id", "user_id", "entity_type", "entity_id")

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field in self._CORRELATION_FIELDS:
            if (value := getattr(record, field, None)) is not None:
                payload[field] = value

        if record.exc_info:
            payload["exception"] = scrub(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, default=str)
