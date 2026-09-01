"""
The console's billing half: invoices, receivables, deliveries, reconciliation
and the AI surfaces.

🔴 **Read-heavy, write-narrow.** Nothing here issues, cancels or records a
payment. Those live on the API behind their confirmation flows, and a console
button that skipped the pre-issue checks would be exactly the one an operator
reaches for under pressure. The two things this module writes — resolving a
contradiction, approving a tax code — go through the same domain services the
API uses.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import and_, func, or_, select

from backend.admin.rendering import render
from backend.admin.router import PER_PAGE, _csrf, _offset, _page, _paged
from backend.admin.security import AdminUser, check_csrf
from backend.deps import SessionDep
from backend.models.billing import BillingEntity, Invoice
from backend.models.business import Organisation
from backend.models.copilot import AiProposal
from backend.models.invoice_ops import (
    InvoiceCheckRun,
    InvoiceDelivery,
    InvoiceExtraction,
    InvoiceGstinCheck,
    PaymentPromise,
    PaymentRequest,
    PaymentWebhookEvent,
    TaxCodeKnowledge,
)

router = APIRouter(prefix="/admin", tags=["admin"], include_in_schema=False)

#: Events that need a person. Named once so the queue page and the nav badge
#: cannot drift apart about what "needs attention" means.
NEEDS_ATTENTION = ("unmatched", "replayed", "signature_failed", "error", "pending")


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


@router.get("/invoices/", response_class=HTMLResponse, name="admin_invoices")
async def invoices(
    request: Request,
    session: SessionDep,
    caller: AdminUser,
    q: str = Query(default=""),
    invoice_status: str = Query(default=""),
    entity: str = Query(default=""),
    fy: str = Query(default=""),
) -> HTMLResponse:
    """The register (INVOICE.md §6.2), with a totals row over the whole filter."""
    conditions = [Invoice.is_deleted.is_(False)]
    if q.strip():
        pattern = f"%{q.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(Invoice.buyer_name).like(pattern),
                func.lower(func.coalesce(Invoice.invoice_no, "")).like(pattern),
                func.lower(func.coalesce(Invoice.buyer_gstin, "")).like(pattern),
            )
        )
    if invoice_status:
        conditions.append(Invoice.status == invoice_status)
    if entity:
        conditions.append(Invoice.entity_code == entity)
    if fy:
        conditions.append(Invoice.financial_year == fy)

    where = and_(*conditions)
    total = await session.scalar(select(func.count(Invoice.id)).where(where)) or 0
    rows = list(
        await session.scalars(
            select(Invoice)
            .where(where)
            .order_by(Invoice.invoice_date.desc(), Invoice.created_at.desc())
            .offset(_offset(request))
            .limit(PER_PAGE)
        )
    )

    # 🔴 Totals over the whole filtered set, not the page. A totals row that
    # sums fifty rows out of a thousand is a number somebody will quote.
    sums = (
        await session.execute(
            select(
                func.coalesce(func.sum(Invoice.taxable_value), 0),
                func.coalesce(func.sum(Invoice.tax_amount), 0),
                func.coalesce(func.sum(Invoice.total_value), 0),
            ).where(where)
        )
    ).one()

    financial_years = list(
        await session.scalars(
            select(Invoice.financial_year)
            .where(Invoice.financial_year.is_not(None))
            .distinct()
            .order_by(Invoice.financial_year.desc())
        )
    )
    entities = list(await session.scalars(select(BillingEntity.code).distinct()))

    return await _page(
        request,
        session,
        "invoices.html",
        section="invoices",
        caller=caller,
        page=_paged(request, total, rows),
        sums={"taxable": sums[0], "tax": sums[1], "total": sums[2]},
        financial_years=financial_years,
        entities=entities,
        filters={"q": q, "invoice_status": invoice_status, "entity": entity, "fy": fy},
    )


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse, name="admin_invoice_detail")
async def invoice_detail(
    invoice_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    caller: AdminUser,
) -> HTMLResponse:
    """
    One invoice with everything attached: lines, payments, the checks that ran
    at issue, GSTIN evidence, deliveries, payment requests and AI proposals.

    🔴 The check run marked `is_issue_evidence` answers "what did we know when
    we issued this". It is immutable, and so is the GSTIN check beside it —
    re-verifying next year writes a new row rather than changing what this
    document was checked against.
    """
    from backend.domain.checks import run_checks

    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        return HTMLResponse(
            render("not_found.html", what="invoice", caller=caller, csrf_token=_csrf(request)),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    organisation = (
        await session.scalar(select(Organisation).where(Organisation.id == invoice.organisation_id))
        if invoice.organisation_id
        else None
    )

    check_runs = list(
        await session.scalars(
            select(InvoiceCheckRun)
            .where(InvoiceCheckRun.invoice_id == invoice.id)
            .order_by(InvoiceCheckRun.ran_at.desc())
            .limit(10)
        )
    )
    gstin_checks = list(
        await session.scalars(
            select(InvoiceGstinCheck)
            .where(InvoiceGstinCheck.invoice_id == invoice.id)
            .order_by(InvoiceGstinCheck.created_at.desc())
        )
    )
    deliveries_rows = list(
        await session.scalars(
            select(InvoiceDelivery)
            .where(InvoiceDelivery.invoice_id == invoice.id)
            .order_by(InvoiceDelivery.created_at.desc())
        )
    )
    payment_requests = list(
        await session.scalars(
            select(PaymentRequest)
            .where(PaymentRequest.invoice_id == invoice.id)
            .order_by(PaymentRequest.created_at.desc())
        )
    )
    promises = list(
        await session.scalars(
            select(PaymentPromise)
            .where(PaymentPromise.invoice_id == invoice.id)
            .order_by(PaymentPromise.promised_on.desc())
        )
    )
    proposals_rows = list(
        await session.scalars(
            select(AiProposal)
            .where(AiProposal.invoice_id == invoice.id)
            .order_by(AiProposal.created_at.desc())
        )
    )

    # Live checks, so the page shows the current state rather than only what
    # was true at issue. A draft edited since is exactly the interesting case.
    live = await run_checks(session, invoice)

    return await _page(
        request,
        session,
        "invoice_detail.html",
        section="invoices",
        caller=caller,
        invoice=invoice,
        organisation=organisation,
        live=live,
        check_runs=check_runs,
        gstin_checks=gstin_checks,
        deliveries=deliveries_rows,
        payment_requests=payment_requests,
        promises=promises,
        proposals=proposals_rows,
    )


# ---------------------------------------------------------------------------
# Receivables
# ---------------------------------------------------------------------------


@router.get("/receivables/", response_class=HTMLResponse, name="admin_receivables")
async def receivables(request: Request, session: SessionDep, caller: AdminUser) -> HTMLResponse:
    """
    Ageing and the collection ranking, from the same domain the API serves.

    🔴 One implementation. A console computing its own ageing would eventually
    disagree with the API's at a bucket boundary, and the number people trust
    is whichever they saw last.
    """
    from backend.domain import receivables as service
    from backend.domain.scoping import EntityScope

    entity_ids = list(await session.scalars(select(BillingEntity.id)))
    scope = EntityScope(caller, entity_ids)

    rows = await service.ageing_rows(session, scope)
    return await _page(
        request,
        session,
        "receivables.html",
        section="receivables",
        caller=caller,
        summary=service.summarise(rows),
        rows=rows,
        by_buyer=await service.by_buyer(session, scope, rows),
        priorities={row.invoice_id: service.collection_priority(row) for row in rows},
    )


# ---------------------------------------------------------------------------
# Deliveries and reconciliation
# ---------------------------------------------------------------------------


@router.get("/deliveries/", response_class=HTMLResponse, name="admin_deliveries")
async def deliveries(
    request: Request,
    session: SessionDep,
    caller: AdminUser,
    delivery_status: str = Query(default=""),
) -> HTMLResponse:
    """
    The outbox: every attempt to put a document in front of a customer.

    🔴 Each row names the PDF hash it carried. A resend after a re-render is a
    different artifact, and "which document did they actually receive" is
    answerable only because each attempt recorded what it carried rather than
    pointing at whatever the invoice holds now.
    """
    conditions = []
    if delivery_status:
        conditions.append(InvoiceDelivery.status == delivery_status)
    where = and_(*conditions) if conditions else True

    total = await session.scalar(select(func.count(InvoiceDelivery.id)).where(where)) or 0
    rows = list(
        await session.scalars(
            select(InvoiceDelivery)
            .where(where)
            .order_by(InvoiceDelivery.created_at.desc())
            .offset(_offset(request))
            .limit(PER_PAGE)
        )
    )

    invoice_numbers: dict[uuid.UUID, str | None] = {}
    if rows:
        invoice_numbers = dict(
            (
                await session.execute(
                    select(Invoice.id, Invoice.invoice_no).where(
                        Invoice.id.in_([r.invoice_id for r in rows])
                    )
                )
            ).all()
        )

    return await _page(
        request,
        session,
        "deliveries.html",
        section="deliveries",
        caller=caller,
        page=_paged(request, total, rows),
        invoice_numbers=invoice_numbers,
        filters={"delivery_status": delivery_status},
    )


@router.get("/reconciliation/", response_class=HTMLResponse, name="admin_reconciliation")
async def reconciliation(request: Request, session: SessionDep, caller: AdminUser) -> HTMLResponse:
    """
    Payment events that need a person: unmatched, replayed or unsigned.

    🔴 Oldest first. A queue sorted newest-first is a queue where the oldest
    problem is never reached — and an unmatched event is money that arrived
    against no invoice, which does not improve with age.
    """
    where = PaymentWebhookEvent.processing_result.in_(NEEDS_ATTENTION)

    total = await session.scalar(select(func.count(PaymentWebhookEvent.id)).where(where)) or 0
    rows = list(
        await session.scalars(
            select(PaymentWebhookEvent)
            .where(where)
            .order_by(PaymentWebhookEvent.received_at.asc())
            .offset(_offset(request))
            .limit(PER_PAGE)
        )
    )
    recent = list(
        await session.scalars(
            select(PaymentWebhookEvent)
            .where(PaymentWebhookEvent.processing_result == "processed")
            .order_by(PaymentWebhookEvent.received_at.desc())
            .limit(10)
        )
    )

    return await _page(
        request,
        session,
        "reconciliation.html",
        section="reconciliation",
        caller=caller,
        page=_paged(request, total, rows),
        recent=recent,
    )


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------


@router.get("/proposals/", response_class=HTMLResponse, name="admin_proposals")
async def proposals(
    request: Request,
    session: SessionDep,
    caller: AdminUser,
    proposal_status: str = Query(default=""),
) -> HTMLResponse:
    """
    Every AI proposal, including the refused ones.

    🔴 The refusals are the point of this page. A proposal that failed with
    `refused: issue an invoice` is the trust boundary working, and a refusal
    nobody counts is a refusal nobody can prove kept happening.
    """
    conditions = []
    if proposal_status:
        conditions.append(AiProposal.status == proposal_status)
    where = and_(*conditions) if conditions else True

    total = await session.scalar(select(func.count(AiProposal.id)).where(where)) or 0
    rows = list(
        await session.scalars(
            select(AiProposal)
            .where(where)
            .order_by(AiProposal.created_at.desc())
            .offset(_offset(request))
            .limit(PER_PAGE)
        )
    )

    by_status = dict(
        (
            await session.execute(
                select(AiProposal.status, func.count(AiProposal.id)).group_by(AiProposal.status)
            )
        ).all()
    )
    refusals = await session.scalar(
        select(func.count(AiProposal.id)).where(AiProposal.error.like("refused:%"))
    )

    return await _page(
        request,
        session,
        "proposals.html",
        section="proposals",
        caller=caller,
        page=_paged(request, total, rows),
        by_status=by_status,
        refusals=refusals or 0,
        filters={"proposal_status": proposal_status},
    )


@router.get("/extractions/", response_class=HTMLResponse, name="admin_extractions")
async def extractions(request: Request, session: SessionDep, caller: AdminUser) -> HTMLResponse:
    """
    What the model read off uploaded documents, beside what was accepted.

    🔴 `extraction_path` is the column to scan. A vision reading of a
    computer-generated PDF means a perfect text layer was thrown away and then
    reconstructed — and the reconstruction is where the errors come from. A
    measured example: an 11B vision model returned a complete fictional invoice
    rather than failing.
    """
    total = await session.scalar(select(func.count(InvoiceExtraction.id))) or 0
    rows = list(
        await session.scalars(
            select(InvoiceExtraction)
            .order_by(InvoiceExtraction.created_at.desc())
            .offset(_offset(request))
            .limit(PER_PAGE)
        )
    )

    by_status = dict(
        (
            await session.execute(
                select(InvoiceExtraction.status, func.count(InvoiceExtraction.id)).group_by(
                    InvoiceExtraction.status
                )
            )
        ).all()
    )

    return await _page(
        request,
        session,
        "extractions.html",
        section="extractions",
        caller=caller,
        page=_paged(request, total, rows),
        by_status=by_status,
    )


@router.get("/tax-codes/", response_class=HTMLResponse, name="admin_tax_codes")
async def tax_codes(request: Request, session: SessionDep, caller: AdminUser) -> HTMLResponse:
    """
    Effective-dated HSN/SAC knowledge and its review state.

    🔴 Only an approved record may be presented as verified, and approving one
    is a compliance action with a named reviewer — the database refuses
    `approved` without one.
    """
    from backend.domain.scoping import KNOWLEDGE_APPROVE

    rows = list(
        await session.scalars(
            select(TaxCodeKnowledge).order_by(
                TaxCodeKnowledge.code, TaxCodeKnowledge.effective_from.desc()
            )
        )
    )

    return await _page(
        request,
        session,
        "tax_codes.html",
        section="tax_codes",
        caller=caller,
        records=rows,
        may_approve=caller.user.role in KNOWLEDGE_APPROVE,
        today=date.today(),
    )


@router.post("/tax-codes/{record_id}/approve", name="admin_tax_code_approve")
async def approve_tax_code(
    record_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    caller: AdminUser,
    csrf_token: Annotated[str, Form()] = "",
    reviewer_name: Annotated[str, Form()] = "",
) -> Response:
    """🔴 The console's one path to `is_verified`, through the same service."""
    request.state.csrf_token = csrf_token
    check_csrf(request)

    from backend.domain import knowledge
    from backend.domain.scoping import KNOWLEDGE_APPROVE, EntityScope, require

    require(caller, KNOWLEDGE_APPROVE, "approve a tax-code record")

    entity_ids = list(await session.scalars(select(BillingEntity.id)))
    await knowledge.approve(
        session, EntityScope(caller, entity_ids), record_id, reviewer_name=reviewer_name
    )
    return RedirectResponse("/admin/tax-codes/", status_code=status.HTTP_303_SEE_OTHER)
