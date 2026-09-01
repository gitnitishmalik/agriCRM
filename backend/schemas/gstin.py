"""GSTIN verification shapes."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class LocalCheckOut(BaseModel):
    """
    Layer one only.

    🔴 There is deliberately no field here a UI could render as "GST-verified".
    `valid` means well-formed; whether the registration is active is a
    different question with a different endpoint, and conflating them is the
    defect this module exists to prevent.
    """

    supplied: str
    normalised: str | None
    valid: bool
    is_govt_uin: bool
    state_code: str | None
    state_name: str | None
    message: str | None
    note: str


class VerificationCreate(BaseModel):
    billing_entity: uuid.UUID
    gstin: str = Field(min_length=1, max_length=32)
    govt_uin: bool = False
    #: What **Verify again** sets. Bypasses the cache; a registration can be
    #: cancelled without notice.
    force: bool = False


class VerificationOut(BaseModel):
    id: str
    gstin: str
    provider: str
    provider_reference: str | None
    status: str
    #: 🔴 True only for `valid_active`. Computed server-side so a client cannot
    #: arrive at it by testing `status != "error"`, which would show a
    #: cancelled registration as fine.
    is_verified: bool
    #: True when the provider could not be reached. Displayed as its own state,
    #: never folded into "not verified" and never into "valid".
    is_unavailable: bool
    legal_name: str | None
    trade_name: str | None
    registration_type: str | None
    taxpayer_status: str | None
    effective_from: str | None
    cancellation_date: str | None
    principal_address: str | None
    state_code: str | None
    checked_at: str
    expires_at: str | None
    age_days: int
    raw_response_sha256: str | None
    error_code: str | None
    error_detail: str | None
    label: str


class GstinCheckOut(BaseModel):
    gstin: str | None
    local: LocalCheckOut
    live: VerificationOut | None
    #: Field-by-field, registry against the CRM record. Reported, never applied.
    differences: list[dict[str, Any]]
    #: 'warn' | 'require_current' — this customer's policy.
    policy: str
    blocks_issue: bool
    overridden: bool = False


class GstinOverrideRequest(BaseModel):
    """
    🔴 A reason is mandatory, and it is stored on an immutable row with the
    actor and the time. An override nobody can review afterwards is not a
    control, it is a bypass.
    """

    reason: str = Field(min_length=10, max_length=1000)


class UseVerifiedResult(BaseModel):
    invoice_id: uuid.UUID
    verification: VerificationOut
    changes: list[dict[str, Any]]
