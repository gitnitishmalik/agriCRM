"""
Personal data: who may see it in full, and what that costs them.

🔴 R9 and R10 in one module, because they are one control. Masking without an
audit trail means nobody can answer "who read this"; an audit trail without
masking means the answer is "everyone, continuously", which is the same as no
answer. `admin/rendering.py` already applies this to the server-rendered
console — this is the same rule for the JSON API, sharing the role set so the
two cannot drift apart.

The design choice worth naming: **unmasking is a request parameter, not a
response the client filters.** A payload that carried the full number and let
the UI hide it would be a masking control that any `curl` walks straight
through, and the audit log would record a view that did not happen alongside
one that did.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.deps import Caller

# ---------------------------------------------------------------------------
# Who may unmask
# ---------------------------------------------------------------------------

#: 🔴 R9 — the `contact.view_full` capability. Deliberately the same three
#: roles as `domain.scoping.BILLING_OVERRIDE`: the set of people who already
#: take personal responsibility for a control being off. `field_agent` is
#: absent, and that is the point — an agent works from a work order naming the
#: people they are visiting, not from a searchable directory of mobiles.
CONTACT_VIEW_FULL = frozenset({"data_ops", "compliance", "admin"})

#: 🔴 R10 — above this many PII records in one response, the caller must type
#: a reason and the read is flagged. Doc 12 puts the alert here; the count is
#: of rows actually returned, not of rows requested, because a paginated walk
#: is the shape an exfiltration takes.
BULK_PII_THRESHOLD = 1_000


def may_unmask(caller: Caller) -> bool:
    return caller.user.role in CONTACT_VIEW_FULL


def require_unmask(caller: Caller) -> None:
    """Raise unless the caller holds `contact.view_full`."""
    if not may_unmask(caller):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Your role ({caller.user.role}) cannot view full contact details. "
            "Unmasking requires the contact.view_full capability and is "
            "recorded in the data access log.",
        )


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------


def mask_phone(value: str) -> str:
    """
    `+91*****6096`.

    The last four digits survive because they are what a person uses to
    recognise a number they already hold, and they are not enough to dial it.
    Identical to `admin.rendering.mask_phone` — a test asserts the two agree,
    because two masks that differ by one digit are one bug away from one of
    them being reversible.
    """
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"+91*****{digits[-4:]}" if len(digits) >= 4 else "•••"


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    if not domain:
        return "•••"
    return f"{local[:2]}•••@{domain}"


def mask_value(kind: str, value: str) -> str:
    """Mask by contact kind. An unrecognised kind masks entirely, not partly."""
    if kind in {"mobile", "landline", "whatsapp", "fax"}:
        return mask_phone(value)
    if kind == "email":
        return mask_email(value)
    return "•••"


# ---------------------------------------------------------------------------
# Normalisation
#
# 🔴 CLAUDE.md: phones normalise to E.164 `+91XXXXXXXXXX` and queries run
# against `value_normalised`. Both halves matter — normalising on write and
# then searching `value_raw` finds nothing, which looks like "we don't have
# that number" rather than like a bug.
# ---------------------------------------------------------------------------

_NON_DIGIT = re.compile(r"\D")

#: Indian mobile series. A number outside it is not rejected outright — a
#: landline is a legitimate contact point — but it does not get the mobile
#: normalisation either.
_INDIAN_MOBILE = re.compile(r"^[6-9]\d{9}$")


class NormalisationError(ValueError):
    """A value that cannot be stored in a form the system can query."""


def normalise_phone(value: str, *, country_code: str = "+91") -> str:
    """
    To E.164, or raise.

    🔴 Raises rather than guesses. CLAUDE.md states the rule for area units
    and it applies identically here: a number stored wrong is worse than a
    number rejected, because the rejection is visible at import time and the
    wrong number surfaces as an undeliverable message months later, counted
    against the WhatsApp quality rating.
    """
    digits = _NON_DIGIT.sub("", value)

    # Order matters, and both forms occur in the same spreadsheet column.
    # A trunk zero can precede the country code ("091 98765 43210"), so it is
    # stripped first; an Indian mobile never begins with 0, which is what makes
    # that safe rather than a guess.
    digits = digits.lstrip("0")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]

    if len(digits) != 10:
        raise NormalisationError(
            f"{value!r} is not a ten-digit Indian number "
            f"(got {len(digits)} digits after stripping formatting)."
        )
    return f"{country_code}{digits}"


def normalise_email(value: str) -> str:
    cleaned = value.strip().lower()
    if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        raise NormalisationError(f"{value!r} is not an email address.")
    return cleaned


def normalise(kind: str, value: str, *, country_code: str = "+91") -> str:
    if kind == "email":
        return normalise_email(value)
    return normalise_phone(value, country_code=country_code)


def is_indian_mobile(normalised: str) -> bool:
    """Whether an E.164 value is in the mobile series — i.e. WhatsApp-capable."""
    return bool(_INDIAN_MOBILE.match(normalised.removeprefix("+91")))


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------


async def log_access(
    session: AsyncSession,
    *,
    caller: Caller,
    action: str,
    entity_type: str,
    record_count: int,
    filters: dict[str, Any] | None = None,
    reason: str | None = None,
    ip_address: str | None = None,
) -> None:
    """
    Write `audit.data_access_log`.

    🔴 `caller.user.public_id`, never the integer pk — CLAUDE.md: the business
    schemas carry no FK back to Django's auth tables, and every user reference
    in the DDL is typed `uuid`.

    Raw SQL rather than a mapped class, deliberately. The table is
    RANGE-partitioned on `occurred_at` with a composite key, and giving it an
    ORM identity invites somebody to query it, update a row, or add a
    relationship — none of which an append-only audit log should permit.
    """
    await session.execute(
        text(
            """
            INSERT INTO audit.data_access_log
                (actor_user_id, action, entity_type, record_count,
                 filter_json, reason, ip_address)
            VALUES
                (:actor, :action, :entity_type, :record_count,
                 CAST(:filter_json AS jsonb), :reason, CAST(:ip AS inet))
            """
        ),
        {
            "actor": str(caller.user.public_id),
            "action": action,
            "entity_type": entity_type,
            "record_count": record_count,
            "filter_json": _json(filters or {}),
            "reason": reason,
            "ip": ip_address,
        },
    )


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps({k: v for k, v in value.items() if v is not None}, default=str)


def check_bulk_reason(record_count: int, reason: str | None) -> str | None:
    """
    🔴 R10. Over the threshold, a typed reason is mandatory.

    Returns the reason so the caller can pass it straight to `log_access`.
    A blank string is not a reason; neither is one short enough to be a
    keystroke, which is why the floor is a sentence rather than a character.
    """
    if record_count < BULK_PII_THRESHOLD:
        return reason

    if reason is None or len(reason.strip()) < 15:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Reading {record_count:,} personal records at once requires a "
            f"stated reason of at least 15 characters (R10). The read is "
            f"recorded against your account and raises an alert.",
        )
    return reason.strip()


def actor_ip(request: Any) -> str | None:
    """The caller's address, for the audit row. None rather than a guess."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    return client.host if client else None


__all__ = [
    "BULK_PII_THRESHOLD",
    "CONTACT_VIEW_FULL",
    "PII_SOURCE_KINDS",
    "NormalisationError",
    "actor_ip",
    "check_bulk_reason",
    "is_indian_mobile",
    "log_access",
    "mask_email",
    "mask_phone",
    "mask_value",
    "may_unmask",
    "normalise",
    "normalise_email",
    "normalise_phone",
    "require_pii_source",
    "require_unmask",
]


# ---------------------------------------------------------------------------
# 🔴 R4 — the gate personal data enters through
# ---------------------------------------------------------------------------

#: The four routes Doc 05 permits for personal data, plus the two batch kinds
#: that need an approved source row behind them.
#:
#: 🔴 What is *absent* is the control. `public_registry`, `official_website`,
#: `open_government_data` and `industry_directory` are all legitimate sources
#: of institutional facts — an FPO name, a CIN, a registration date, a
#: director's DIN published by the MCA under the Companies Act — and none of
#: them is a lawful basis for a named person's mobile number. A farmer listed
#: in a state subsidy portal did not publish that themselves, so the DPDP
#: s.3(c)(ii) "publicly available" exemption does not reach them.
#:
#: Widening this set is a decision for a named person with legal advice,
#: recorded by editing the `dq.source` row it applies to.
PII_SOURCE_KINDS = frozenset(
    {
        "partner_agreement",
        "field_collection",
        "inbound_signup",
        "theta_analytics",
        "purchased_licensed",
        "manual_entry",
    }
)


async def require_pii_source(session: AsyncSession, source_id: int) -> Any:
    """
    Resolve a `dq.source` and refuse unless it may carry personal data.

    Three separate conditions, and the error says which one failed, because
    "rejected" with no reason is what makes people work around a control
    rather than fix the source row:

    1. The source exists.
    2. It is approved — R1, the same assertion a collector makes.
    3. Its kind is one personal data may lawfully arrive through — R4.

    `contains_pii` is then set as a consequence rather than checked: a source
    that has never carried personal data before is not disqualified from
    carrying it now, provided its *kind* permits it. The flag records what has
    happened; the kind governs what may.
    """
    from backend.models.business import Source

    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"No source with id {source_id}. Personal data must name the "
            f"dq.source row it arrived through (R4).",
        )
    if not source.is_approved:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Source {source.code!r} is not approved. A source with no "
            f"compliance sign-off cannot supply personal data (R1).",
        )
    if source.kind not in PII_SOURCE_KINDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Source {source.code!r} is a {source.kind!r} source, which may "
            f"carry institutional facts but not personal data (R4). "
            f"Personal data enters only via: "
            f"{', '.join(sorted(PII_SOURCE_KINDS))}.",
        )
    return source
