"""Shapes for pre-issue checks and their acknowledgement."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CheckResultOut(BaseModel):
    code: str
    severity: str
    title: str
    explanation: str
    blocks_issue: bool
    evidence: dict[str, Any]
    #: 🔴 True when the check could not run for want of data that does not
    #: exist yet. Render it distinctly — "not checked" is not "checked and
    #: fine", and collapsing the two is how a gap becomes a false assurance.
    not_available: bool


class CheckReportOut(BaseModel):
    invoice_id: str
    #: Quote this back when issuing. A draft edited after a clean run produces
    #: a different hash and is re-checked rather than issued on a stale pass.
    invoice_sha256: str
    can_issue: bool
    blocking_count: int
    warning_count: int
    unacknowledged_warning_count: int
    acknowledged_codes: list[str]
    results: list[CheckResultOut]


class AcknowledgeRequest(BaseModel):
    """
    Accept one non-blocking warning, with a reason.

    🔴 There is no acknowledgement path for a blocking error. Those are fixed,
    or overridden through the GSTIN override, which is separately permissioned
    and records the same three things: who, when, why.
    """

    code: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=1000)


class IssueRequest(BaseModel):
    """
    Issue confirmation.

    `invoice_sha256` is the check run being relied on. Omitting it re-runs the
    checks and refuses if anything blocks — which is the safe default, so the
    field is optional rather than required.
    """

    invoice_sha256: str | None = None
    #: Non-blocking warnings the user saw and accepted on the confirmation
    #: screen. Recorded with their reason.
    acknowledge: list[AcknowledgeRequest] = []
