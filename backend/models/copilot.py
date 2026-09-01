"""
`crm.ai_proposal` and the evaluation tables — the AI trust boundary, stored.

🔴 The design rule these tables encode: an AI action is a *record* before it is
an effect. The copilot never mutates an invoice. It writes a proposal, a human
reads the diff and confirms a specific hash, and only then does a deterministic
applier touch a draft. Every step is a column here, so "who agreed to what, and
against which bytes" survives the conversation that produced it.

INVOICE.md §12.2 and §12.5.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db import Base
from backend.models.types import AI_PROPOSAL_ACTION, AI_PROPOSAL_STATUS, KNOWLEDGE_REVIEW_STATUS

#: The states a proposal can be in. Terminal states never move again.
PROPOSAL_STATUSES = ("pending", "confirmed", "applied", "rejected", "expired", "failed")
TERMINAL_PROPOSAL_STATUSES = frozenset({"applied", "rejected", "expired", "failed"})

#: 🔴 Every action the copilot is allowed to name. Not a convenience constant —
#: this is the allow-list, mirrored from `crm.ai_proposal_action`, and it is
#: why "issue this invoice" cannot be expressed as a proposal at all.
PROPOSAL_ACTIONS = (
    "create_draft",
    "update_draft",
    "suggest_organisation_update",
    "explain_total",
)

#: Actions that write. `explain_total` and `suggest_organisation_update`
#: produce reading material and nothing else.
MUTATING_ACTIONS = frozenset({"create_draft", "update_draft"})


class AiProposal(Base):
    __tablename__ = "ai_proposal"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    #: 🔴 The isolation boundary, taken from the caller's session and never
    #: from the request body. A proposal is readable, confirmable and
    #: applicable only by someone scoped to the same billing entity.
    billing_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.billing_entity.id"))
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(AI_PROPOSAL_ACTION)
    status: Mapped[str] = mapped_column(AI_PROPOSAL_STATUS, default="pending")

    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.invoice.id"), nullable=True
    )
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 🔴 What a confirmation binds to. Computed over the action, the patch and
    #: the before-snapshot together, so a draft edited between proposal and
    #: confirmation invalidates the confirmation instead of quietly applying a
    #: diff against state the human never saw.
    proposal_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    input_sha256: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    evidence: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    before_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    proposed_patch: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    missing_fields: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_PROPOSAL_STATUSES


class AiEvaluationCase(Base):
    """
    One golden case. 🔴 Redacted fixtures only — never a customer's document.

    `is_critical` is what stops a bad GSTIN hiding inside an average: the CI
    gate reads critical cases separately and requires 100% on them
    (INVOICE.md §12.7).
    """

    __tablename__ = "ai_evaluation_case"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    input_fixture: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB)
    regression_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[str] = mapped_column(KNOWLEDGE_REVIEW_STATUS, default="approved")
    provenance: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class AiEvaluationRun(Base):
    __tablename__ = "ai_evaluation_run"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cases_total: Mapped[int] = mapped_column(Integer, default=0)
    cases_passed: Mapped[int] = mapped_column(Integer, default=0)
    critical_total: Mapped[int] = mapped_column(Integer, default=0)
    critical_passed: Mapped[int] = mapped_column(Integer, default=0)
    #: 🔴 Any non-zero value fails the release gate. A model that asked to
    #: issue, cancel, pay or send once will ask again.
    unsafe_requests: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class AiEvaluationResult(Base):
    __tablename__ = "ai_evaluation_result"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.ai_evaluation_run.id"))
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.ai_evaluation_case.id"))
    passed: Mapped[bool] = mapped_column(Boolean)
    #: Abstention is a pass, not a failure. A model that says "I cannot read
    #: this rate" is behaving correctly; one that guesses is not.
    abstained: Mapped[bool] = mapped_column(Boolean, default=False)
    field_results: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
