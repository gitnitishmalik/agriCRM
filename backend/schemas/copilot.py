"""Request and response shapes for the invoice copilot."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ProposalCreate(BaseModel):
    """
    Ask the copilot for a draft.

    🔴 `billing_entity` names the issuing company, and it is checked against
    the caller's scope rather than trusted. `invoice` targets an existing
    unnumbered draft; omit it to propose a new one.
    """

    request: str = Field(min_length=1, max_length=4000)
    billing_entity: uuid.UUID
    invoice: uuid.UUID | None = None
    action: str = "create_draft"


class ProposalConfirm(BaseModel):
    """
    🔴 The hash is required, not optional.

    A confirm endpoint that accepts a missing hash has an opt-out, and an
    opt-out from "confirm exactly this" is the same as not having it.
    """

    proposal_sha256: str = Field(min_length=64, max_length=64)


class ProposalReject(BaseModel):
    reason: str | None = None


class ProposalOut(BaseModel):
    id: uuid.UUID
    status: str
    action: str
    billing_entity: uuid.UUID
    invoice: uuid.UUID | None
    #: Quote this back to confirm. Hex of the stored `bytea`.
    proposal_sha256: str
    model: str | None
    provider: str | None
    prompt_version: str | None
    evidence: list[Any]
    before_snapshot: dict[str, Any]
    proposed_patch: dict[str, Any]
    warnings: list[Any]
    missing_fields: list[str]
    confidence: Decimal | None
    expires_at: datetime
    created_at: datetime
    confirmed_at: datetime | None
    applied_at: datetime | None
    error: str | None
    #: Rendered field-by-field so a panel does not have to diff two blobs.
    diff: list[dict[str, Any]] = []


class ApplyResult(BaseModel):
    proposal: ProposalOut
    invoice: uuid.UUID
    #: What actually changed, field by field, after the patch was applied.
    applied_diff: list[dict[str, Any]]


class TraceLine(BaseModel):
    line_no: int
    description: str
    quantity: str
    unit: str
    quantity_ha: str | None
    rate: str
    rate_is_tax_inclusive: bool
    taxable: str
    tax: str
    total: str
    explanation: str


class CalculationTrace(BaseModel):
    """
    🔴 Server-computed, every figure. A model may paraphrase this and may not
    replace a number in it (INVOICE.md §12.3 C).
    """

    invoice_id: str
    invoice_no: str | None
    tax_treatment: str
    tax_rate_pct: str
    lines: list[TraceLine]
    taxable_value: str
    tax_amount: str
    total_value: str
    amount_in_words: str
    rounding: str
    treatment_evidence: dict[str, Any]
    header_agrees_with_lines: bool
