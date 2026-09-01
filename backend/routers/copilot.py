"""
`/api/v1/invoice-copilot/` — propose, confirm, apply, reject, explain.

🔴 **Five endpoints, no generic tool runner** (INVOICE.md §12.6). The actions
the copilot can take are the routes that exist, and each one calls a specific
domain function. There is deliberately no endpoint that takes a tool name.

🔴 **Create and apply are separate calls, and issue is not here at all.** The
UI has a "Create draft" button and an "Issue invoice" button, and they are
never chained — the exit gate for phase I-7 requires a test proving the copilot
cannot issue, and the structural reason it cannot is that no route in this file
allocates a number.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from backend.deps import SessionDep
from backend.domain import proposals as service
from backend.domain.scoping import BILLING_READ, BILLING_WRITE, Scope
from backend.models.billing import Invoice
from backend.models.copilot import AiProposal
from backend.schemas.copilot import (
    ApplyResult,
    CalculationTrace,
    ProposalConfirm,
    ProposalCreate,
    ProposalOut,
    ProposalReject,
)

router = APIRouter(prefix="/api/v1/invoice-copilot", tags=["invoice-copilot"])


def _out(proposal: AiProposal, *, diff: list | None = None) -> ProposalOut:
    return ProposalOut(
        id=proposal.id,
        status=proposal.status,
        action=proposal.action,
        billing_entity=proposal.billing_entity_id,
        invoice=proposal.invoice_id,
        proposal_sha256=proposal.proposal_sha256.hex(),
        model=proposal.model,
        provider=proposal.provider,
        prompt_version=proposal.prompt_version,
        evidence=proposal.evidence,
        before_snapshot=proposal.before_snapshot,
        proposed_patch=proposal.proposed_patch,
        warnings=proposal.warnings,
        missing_fields=list(proposal.missing_fields),
        confidence=proposal.confidence,
        expires_at=proposal.expires_at,
        created_at=proposal.created_at,
        confirmed_at=proposal.confirmed_at,
        applied_at=proposal.applied_at,
        error=proposal.error,
        diff=diff if diff is not None else _preview_diff(proposal),
    )


def _preview_diff(proposal: AiProposal) -> list[dict]:
    """
    What the patch *would* change, rendered before it is applied.

    Computed from the stored before-snapshot rather than from the live invoice,
    so the diff a user confirms is the diff they were shown — the same reason
    the snapshot is inside the hash.
    """
    before = proposal.before_snapshot or {}
    patch = proposal.proposed_patch or {}
    rows: list[dict] = []

    for key, value in patch.items():
        if key == "lines":
            rows.append(
                {
                    "field": "lines",
                    "before": before.get("lines", []),
                    "after": value,
                }
            )
            continue
        rows.append({"field": key, "before": before.get(key), "after": value})

    return rows


@router.post(
    "/proposals/",
    response_model=ProposalOut,
    status_code=status.HTTP_201_CREATED,
    name="copilot_proposal_create",
)
async def create_proposal(
    payload: ProposalCreate, session: SessionDep, scope: Scope
) -> ProposalOut:
    """
    Text (or a transcript) in, evidence-backed proposal out. Writes nothing.

    A request naming an action the copilot must not take — issue, cancel, pay,
    send, file — is refused before any provider is called, and the refusal is
    recorded so the evaluation summary can count it.
    """
    scope.require(BILLING_WRITE, "ask the copilot for a draft")

    invoice = None
    if payload.invoice is not None:
        invoice = await session.scalar(select(Invoice).where(Invoice.id == payload.invoice))
        if invoice is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")

    proposal = await service.create_proposal(
        session,
        scope,
        request_text=payload.request,
        action=payload.action,
        billing_entity_id=payload.billing_entity,
        invoice=invoice,
    )
    return _out(proposal)


@router.get("/proposals/{proposal_id}/", response_model=ProposalOut, name="copilot_proposal_get")
async def get_proposal(proposal_id: uuid.UUID, session: SessionDep, scope: Scope) -> ProposalOut:
    scope.require(BILLING_READ, "read a proposal")
    proposal = await service.load_proposal(session, scope, proposal_id)
    return _out(proposal)


@router.post(
    "/proposals/{proposal_id}/confirm/",
    response_model=ProposalOut,
    name="copilot_proposal_confirm",
)
async def confirm_proposal(
    proposal_id: uuid.UUID, payload: ProposalConfirm, session: SessionDep, scope: Scope
) -> ProposalOut:
    """
    🔴 A named human accepts exactly these bytes.

    Idempotent: confirming twice with the same hash is the same as confirming
    once. Confirming with a different hash is refused, because the draft has
    moved and the diff on screen is no longer the diff being approved.
    """
    scope.require(BILLING_WRITE, "confirm a proposal")
    proposal = await service.load_proposal(session, scope, proposal_id)
    confirmed = await service.confirm_proposal(
        session, scope, proposal, proposal_sha256=payload.proposal_sha256
    )
    return _out(confirmed)


@router.post(
    "/proposals/{proposal_id}/apply/", response_model=ApplyResult, name="copilot_proposal_apply"
)
async def apply_proposal(proposal_id: uuid.UUID, session: SessionDep, scope: Scope) -> ApplyResult:
    """
    Write the confirmed patch to an unnumbered draft.

    🔴 Creates or updates a *draft*, and nothing else. It does not issue, and
    it re-checks the invoice's current state rather than trusting the state the
    proposal remembers — between confirm and apply, somebody may have issued it.
    """
    scope.require(BILLING_WRITE, "apply a proposal")
    proposal = await service.load_proposal(session, scope, proposal_id)
    invoice, diff = await service.apply_proposal(session, scope, proposal)
    return ApplyResult(proposal=_out(proposal, diff=diff), invoice=invoice.id, applied_diff=diff)


@router.post(
    "/proposals/{proposal_id}/reject/",
    response_model=ProposalOut,
    name="copilot_proposal_reject",
)
async def reject_proposal(
    proposal_id: uuid.UUID, payload: ProposalReject, session: SessionDep, scope: Scope
) -> ProposalOut:
    """Decline it. The row stays — a rejection is evidence about the model."""
    scope.require(BILLING_WRITE, "reject a proposal")
    proposal = await service.load_proposal(session, scope, proposal_id)
    rejected = await service.reject_proposal(session, scope, proposal, reason=payload.reason)
    return _out(rejected)


@router.get(
    "/invoices/{invoice_id}/explain/",
    response_model=CalculationTrace,
    name="copilot_explain_total",
)
async def explain_total(
    invoice_id: uuid.UUID, session: SessionDep, scope: Scope
) -> CalculationTrace:
    """
    "Why is this amount what it is?" — answered by arithmetic, not by a model.

    🔴 Every figure here is recomputed server-side from quantity and rate. A
    model may paraphrase the trace; it cannot supply a replacement number,
    because the numbers never pass through one.
    """
    scope.require(BILLING_READ, "read an invoice")
    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")
    scope.check(invoice.billing_entity_id, what="invoice")

    return CalculationTrace(**service.calculation_trace(invoice))
