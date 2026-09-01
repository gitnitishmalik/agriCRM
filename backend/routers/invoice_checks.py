"""
`/api/v1/invoices/{id}/checks/` — run the pre-issue checks, and acknowledge one.

The endpoint is a convenience for the confirmation screen. 🔴 It is *not* the
control: `issue_invoice` runs the same checks itself and refuses on its own
findings, so a client that skips this call, or calls it and ignores the answer,
still cannot issue an invoice with a malformed GSTIN.

That separation is deliberate. A check that only runs when the UI asks is a
check the next UI forgets.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from backend.deps import SessionDep
from backend.domain.checks import run_checks
from backend.domain.scoping import BILLING_READ, BILLING_WRITE, Scope
from backend.models.billing import Invoice
from backend.models.invoice_ops import InvoiceCheckAck, InvoiceCheckRun
from backend.schemas.checks import AcknowledgeRequest, CheckReportOut

router = APIRouter(prefix="/api/v1/invoices", tags=["invoice-checks"])


async def _load(session, scope: Scope, invoice_id: uuid.UUID) -> Invoice:
    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")
    scope.check(invoice.billing_entity_id, what="invoice")
    return invoice


@router.post("/{invoice_id}/checks/", response_model=CheckReportOut, name="invoice_checks_run")
async def run_invoice_checks(
    invoice_id: uuid.UUID, session: SessionDep, scope: Scope
) -> CheckReportOut:
    """
    Run every deterministic check and record the run.

    POST rather than GET because it writes a `crm.invoice_check_run` row — what
    was known, when, and against which version of the draft. That row is the
    answer to "what did we see before we issued this", and a check that leaves
    no trace cannot answer it.
    """
    scope.require(BILLING_READ, "run invoice checks")
    invoice = await _load(session, scope, invoice_id)

    report = await run_checks(session, invoice)

    session.add(
        InvoiceCheckRun(
            invoice_id=invoice.id,
            ran_at=datetime.now(UTC),
            ran_by=scope.user_id,
            invoice_sha256=report.invoice_sha256,
            blocking_count=len(report.blocking),
            warning_count=len(report.warnings),
            results=[result.as_dict() for result in report.results],
            is_issue_evidence=False,
        )
    )
    await session.flush()

    return CheckReportOut(**report.as_dict())


@router.post(
    "/{invoice_id}/checks/acknowledge/",
    response_model=CheckReportOut,
    name="invoice_checks_acknowledge",
)
async def acknowledge_check(
    invoice_id: uuid.UUID,
    payload: AcknowledgeRequest,
    session: SessionDep,
    scope: Scope,
) -> CheckReportOut:
    """
    Accept one non-blocking warning, recording actor, time and reason.

    🔴 A blocking error cannot be acknowledged here. Accepting one would make
    the severity meaningless — the point of `blocks_issue` is that no amount of
    agreeing with it lets the document out. Blocking GSTIN results have their
    own override, which is separately permissioned.
    """
    scope.require(BILLING_WRITE, "acknowledge a warning")
    invoice = await _load(session, scope, invoice_id)

    report = await run_checks(session, invoice)
    match = next((result for result in report.results if result.code == payload.code), None)

    if match is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{payload.code}' is not a current finding on this invoice. It may "
            f"have been resolved by an edit — re-run the checks.",
        )
    if match.blocks_issue:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{payload.code}' blocks issue and cannot be acknowledged away. {match.explanation}",
        )

    existing = await session.scalar(
        select(InvoiceCheckAck).where(
            InvoiceCheckAck.invoice_id == invoice.id,
            InvoiceCheckAck.check_code == payload.code,
            InvoiceCheckAck.acknowledged_by == scope.user_id,
        )
    )
    if existing is None:
        session.add(
            InvoiceCheckAck(
                invoice_id=invoice.id,
                check_code=payload.code,
                severity=match.severity,
                reason=payload.reason.strip(),
                acknowledged_by=scope.user_id,
                acknowledged_at=datetime.now(UTC),
            )
        )
        await session.flush()

    refreshed = await run_checks(session, invoice)
    return CheckReportOut(**refreshed.as_dict())
