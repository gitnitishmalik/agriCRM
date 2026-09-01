"""
GSTIN verification — the two layers, the cache, and the buyer comparison.

INVOICE.md §5.3 and §12.4 describe two layers, and keeping them distinct is the
whole point:

1. **Local.** Normalise, check length, structure, embedded PAN, state code and
   checksum. Deterministic, free, always available, and already implemented in
   `api/gstin.py`.
2. **Live.** Ask an approved provider whether the registration is active, and
   under what name.

🔴 **The UI must never label the local result "GST-verified".** A
checksum-valid GSTIN is a well-formed one, not an active one, and the whole
D1/D2 problem in the historical data is people believing otherwise. The two
results are returned as separate fields for exactly that reason.

🔴 **Cached, deduplicated and TTL'd, but never assumed.** A cached
`valid_active` from five weeks ago is stale, and the pre-issue checks say so.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import gstin as gstin_lib
from backend.domain.hashing import sha256_of
from backend.domain.scoping import EntityScope
from backend.models.billing import Invoice
from backend.models.business import Organisation
from backend.models.invoice_ops import GstinVerification
from backend.providers.gstin_lookup import get_provider

logger = logging.getLogger("backend.verification")

#: 🔴 In-process deduplication of concurrent lookups for the same
#: (entity, GSTIN, provider). Two people opening the same invoice at once
#: should cost one paid lookup, not two — and a provider that rate-limits will
#: reject the second anyway.
_INFLIGHT: dict[tuple[str, str, str], asyncio.Lock] = {}


@dataclass
class LocalCheck:
    """The deterministic layer, on its own."""

    supplied: str
    normalised: str | None
    valid: bool
    is_govt_uin: bool
    state_code: str | None
    state_name: str | None
    message: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "supplied": self.supplied,
            "normalised": self.normalised,
            "valid": self.valid,
            "is_govt_uin": self.is_govt_uin,
            "state_code": self.state_code,
            "state_name": self.state_name,
            "message": self.message,
            "note": (
                "This is a format and checksum check only. It says the number is "
                "well-formed, not that the registration is active — never label it "
                "'GST-verified'."
            ),
        }


def check_locally(value: str, *, allow_govt_uin: bool = False) -> LocalCheck:
    """Layer one. No network, and it runs as the user types."""
    supplied = (value or "").strip()
    try:
        normalised = gstin_lib.validate(supplied, allow_govt_uin=allow_govt_uin)
    except gstin_lib.GSTINError as error:
        code = gstin_lib.state_code(supplied)
        return LocalCheck(
            supplied=supplied,
            normalised=None,
            valid=False,
            is_govt_uin=False,
            state_code=code,
            state_name=gstin_lib.state_name(code) if code else None,
            message=str(error),
        )

    is_uin = allow_govt_uin and not gstin_lib.GSTIN_RE.match(normalised)
    code = gstin_lib.state_code(normalised)
    return LocalCheck(
        supplied=supplied,
        normalised=normalised,
        valid=True,
        is_govt_uin=is_uin,
        state_code=code,
        state_name=gstin_lib.state_name(code) if code else None,
        message=None,
    )


async def _cached(
    session: AsyncSession, *, entity_id: uuid.UUID, gstin: str, provider: str
) -> GstinVerification | None:
    row = await session.scalar(
        select(GstinVerification)
        .where(
            GstinVerification.billing_entity_id == entity_id,
            GstinVerification.gstin == gstin,
            GstinVerification.provider == provider,
        )
        .order_by(GstinVerification.checked_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    if row.expires_at is not None and row.expires_at <= datetime.now(UTC):
        return None
    # 🔴 An unavailable result is never served from cache. Reusing "we could not
    # reach the provider" would turn one outage into a permanent unknown.
    if row.status == "verification_unavailable":
        return None
    return row


async def verify(
    session: AsyncSession,
    scope: EntityScope,
    *,
    billing_entity_id: uuid.UUID,
    gstin: str,
    allow_govt_uin: bool = False,
    force: bool = False,
) -> GstinVerification:
    """
    Verify a GSTIN, reusing a fresh cached result unless `force` is set.

    `force` is what the **Verify again** button calls. It exists because a
    registration can be cancelled without notice, and a customer who matters is
    worth a fresh lookup regardless of the TTL.
    """
    from backend.config import settings

    scope.check(billing_entity_id, what="billing entity")

    local = check_locally(gstin, allow_govt_uin=allow_govt_uin)
    provider = get_provider()

    if not local.valid:
        # 🔴 Never spend a lookup on something the local check rejects, and
        # record the rejection so the invoice's evidence shows it happened.
        row = GstinVerification(
            billing_entity_id=billing_entity_id,
            gstin=(local.supplied or "").upper(),
            provider=provider.name,
            status="invalid_format",
            error_code="invalid_format",
            error_detail=local.message,
            checked_at=datetime.now(UTC),
            requested_by=scope.user_id,
        )
        session.add(row)
        await session.flush()
        return row

    normalised = local.normalised or ""

    if not force:
        cached = await _cached(
            session, entity_id=billing_entity_id, gstin=normalised, provider=provider.name
        )
        if cached is not None:
            return cached

    key = (str(billing_entity_id), normalised, provider.name)
    lock = _INFLIGHT.setdefault(key, asyncio.Lock())

    async with lock:
        # Re-check under the lock: whoever held it may have just done the work.
        if not force:
            cached = await _cached(
                session, entity_id=billing_entity_id, gstin=normalised, provider=provider.name
            )
            if cached is not None:
                return cached

        result = await provider.lookup(normalised)

    ttl = timedelta(hours=settings.gstin_cache_ttl_hours)
    row = GstinVerification(
        billing_entity_id=billing_entity_id,
        gstin=normalised,
        provider=result.provider,
        provider_reference=result.provider_reference,
        status=result.status,
        legal_name=result.legal_name,
        trade_name=result.trade_name,
        registration_type=result.registration_type,
        taxpayer_status=result.taxpayer_status,
        effective_from=result.effective_from,
        cancellation_date=result.cancellation_date,
        principal_address=result.principal_address,
        state_code=result.state_code or normalised[:2],
        # 🔴 The hash, not the body. The reply describes a real business, and
        # the only audit question is "is this the reply we acted on".
        raw_response_sha256=sha256_of(result.raw) if result.raw else None,
        checked_at=datetime.now(UTC),
        # An unavailable result gets no TTL: it must not be served from cache.
        expires_at=(
            None if result.status == "verification_unavailable" else datetime.now(UTC) + ttl
        ),
        error_code=result.error_code,
        error_detail=result.error_detail,
        requested_by=scope.user_id,
    )
    session.add(row)
    await session.flush()
    return row


def compare_with_organisation(
    verification: GstinVerification, organisation: Organisation | None
) -> list[dict[str, Any]]:
    """
    Field-by-field, live identity against the CRM record.

    🔴 Returns differences; it never writes one. "Use verified details" is a
    separate, explicitly confirmed action — silently overwriting a customer
    record with a provider's spelling of their name is how a registry stops
    being something a human curated.
    """
    if organisation is None:
        return []

    differences: list[dict[str, Any]] = []

    def _add(field: str, ours: Any, theirs: Any, note: str) -> None:
        if theirs and ours and str(ours).strip().lower() != str(theirs).strip().lower():
            differences.append({"field": field, "crm": ours, "registry": theirs, "note": note})
        elif theirs and not ours:
            differences.append(
                {
                    "field": field,
                    "crm": None,
                    "registry": theirs,
                    "note": f"The registry has this and the customer record does not. {note}",
                }
            )

    _add(
        "name",
        organisation.name,
        verification.legal_name,
        "A trade name differing from the legal name is normal; a different company is not.",
    )
    _add(
        "gstin",
        organisation.gstin,
        verification.gstin,
        "A customer with a registration per state needs the right one for this invoice.",
    )

    if verification.state_code and organisation.state_id is not None:
        # State ids are LGD codes and GST state codes are not the same
        # numbering, so this is reported rather than compared numerically.
        differences.append(
            {
                "field": "state",
                "crm": organisation.state_id,
                "registry": verification.state_code,
                "note": (
                    "GST state codes and LGD state ids are different numbering "
                    "schemes; check by name rather than by number."
                ),
            }
        )

    return differences


async def evidence_for_invoice(
    session: AsyncSession,
    scope: EntityScope,
    invoice: Invoice,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """
    Everything the issue screen needs about this invoice's buyer GSTIN.

    Both layers, the comparison against the linked customer, and whether the
    result blocks issue under this customer's policy.
    """
    scope.check(invoice.billing_entity_id, what="invoice")

    raw = (invoice.buyer_gstin or "").strip()
    local = check_locally(raw, allow_govt_uin=invoice.buyer_is_govt_uin)

    if not raw:
        return {
            "gstin": None,
            "local": local.as_dict(),
            "live": None,
            "differences": [],
            "policy": "warn",
            "blocks_issue": invoice.tax_treatment in ("igst", "cgst_sgst"),
        }

    verification = await verify(
        session,
        scope,
        billing_entity_id=invoice.billing_entity_id,
        gstin=raw,
        allow_govt_uin=invoice.buyer_is_govt_uin,
        force=force,
    )

    organisation = None
    policy = "warn"
    if invoice.organisation_id is not None:
        organisation = await session.scalar(
            select(Organisation).where(Organisation.id == invoice.organisation_id)
        )
        if organisation is not None:
            policy = organisation.gstin_policy or "warn"

    differences = compare_with_organisation(verification, organisation)

    blocks = verification.status in ("cancelled", "valid_inactive", "invalid_format")
    if policy == "require_current" and verification.status != "valid_active":
        blocks = True

    return {
        "gstin": verification.gstin,
        "local": local.as_dict(),
        "live": serialise(verification),
        "differences": differences,
        "policy": policy,
        "blocks_issue": blocks,
    }


def serialise(row: GstinVerification) -> dict[str, Any]:
    """
    A verification as JSON.

    `is_verified` is computed here rather than left to the client, because
    "active" is the only status that means verified and a UI computing it from
    `status != "error"` would show a cancelled registration as fine.
    """
    age_days = (datetime.now(UTC) - row.checked_at).days
    return {
        "id": str(row.id),
        "gstin": row.gstin,
        "provider": row.provider,
        "provider_reference": row.provider_reference,
        "status": row.status,
        "is_verified": row.status == "valid_active",
        "is_unavailable": row.status == "verification_unavailable",
        "legal_name": row.legal_name,
        "trade_name": row.trade_name,
        "registration_type": row.registration_type,
        "taxpayer_status": row.taxpayer_status,
        "effective_from": row.effective_from.isoformat() if row.effective_from else None,
        "cancellation_date": (row.cancellation_date.isoformat() if row.cancellation_date else None),
        "principal_address": row.principal_address,
        "state_code": row.state_code,
        "checked_at": row.checked_at.isoformat(),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "age_days": age_days,
        "raw_response_sha256": (row.raw_response_sha256.hex() if row.raw_response_sha256 else None),
        "error_code": row.error_code,
        "error_detail": row.error_detail,
        "label": _label(row.status),
    }


def _label(status: str) -> str:
    """
    Wording a UI can print directly.

    🔴 None of these says "verified" unless the registration is active, and
    the unavailable case says what it means rather than shrugging.
    """
    return {
        "valid_active": "Verified active with the registry",
        "valid_inactive": "Registered but not currently active",
        "cancelled": "Registration cancelled",
        "provisional": "Provisional registration",
        "not_found": "No registration found",
        "invalid_format": "Not a valid GSTIN — rejected before any lookup",
        "verification_unavailable": (
            "Could not be checked — the provider was unreachable. This is not the same as valid."
        ),
        "error": "The provider returned an error",
    }.get(status, status)


async def apply_verified_details(
    session: AsyncSession,
    scope: EntityScope,
    invoice: Invoice,
    verification: GstinVerification,
) -> list[dict[str, Any]]:
    """
    "Use verified details" — populate a draft from the registry.

    🔴 A draft only, and only after explicit confirmation at the route. An
    issued invoice's buyer block is a snapshot of what was printed and is never
    rewritten; a customer that moved office must not silently alter a document
    their accounts team already holds.
    """
    scope.check(invoice.billing_entity_id, what="invoice")

    if invoice.status != "draft" or invoice.invoice_no:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This invoice is {invoice.status}"
            + (f" and numbered {invoice.invoice_no}" if invoice.invoice_no else "")
            + ". Verified details populate a draft; an issued document is never "
            "rewritten.",
        )

    if verification.status != "valid_active":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This verification is '{verification.status}' — "
            f"{_label(verification.status)}. Only an active registration's details "
            f"may populate an invoice.",
        )

    changes: list[dict[str, Any]] = []
    for field, value in (
        ("buyer_name", verification.legal_name),
        ("buyer_gstin", verification.gstin),
        ("buyer_address", verification.principal_address),
        ("buyer_state_code", verification.state_code),
    ):
        if not value:
            continue
        before = getattr(invoice, field)
        if before != value:
            changes.append({"field": field, "before": before, "after": value})
            setattr(invoice, field, value)

    if changes:
        invoice.updated_at = datetime.now(UTC)
        invoice.updated_by = scope.user_id
        await session.flush()

    return changes
