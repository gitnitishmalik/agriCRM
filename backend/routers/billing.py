"""
The invoice register (INVOICE.md).

Read paths only for now. Issuing, cancelling and recording a payment are state
transitions with money and a statutory document behind them, and they stay on
the Django service until they are ported deliberately with their own tests —
half a state machine in each of two services is worse than all of it in one.

🔴 Every route depends on `CurrentUser`: authenticated *and* past the second
factor. `tests/test_mfa_boundary.py` walks the router and fails on anything
that is neither gated nor declared in `deps.PRE_MFA`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from backend.deps import CurrentUser, SessionDep, StrictQuery
from backend.models.billing import NOT_OWED_STATUSES, OUTSTANDING_STATUSES, Invoice
from backend.money import format_inr
from backend.schemas.billing import (
    InvoiceDetail,
    InvoicePage,
    InvoiceRow,
    RegisterSummary,
    SummaryDisplay,
)

router = APIRouter(prefix="/api/v1/invoices", tags=["billing"])


def _row(invoice: Invoice) -> InvoiceRow:
    """
    One register row, with its money pre-formatted.

    🔴 Formatted here rather than in the client, for the same reason the Django
    service does it: Indian grouping is 15,78,250.00 and not 1,578,250.00, and
    a second implementation in TypeScript is a second implementation to get
    wrong. The rule lives in `api/money.py` and nowhere else.
    """
    outstanding = invoice.amount_outstanding
    return InvoiceRow(
        id=invoice.id,
        invoice_no=invoice.invoice_no,
        entity_code=invoice.entity_code,
        invoice_date=invoice.invoice_date,
        financial_year=invoice.financial_year,
        buyer_name=invoice.buyer_name,
        status=invoice.status,
        tax_treatment=invoice.tax_treatment,
        taxable_value=invoice.taxable_value,
        tax_amount=invoice.tax_amount,
        total_value=invoice.total_value,
        amount_outstanding=outstanding,
        display={
            "total": format_inr(invoice.total_value),
            "outstanding": format_inr(outstanding),
            "taxable": format_inr(invoice.taxable_value),
            "tax": format_inr(invoice.tax_amount),
            "received": format_inr(invoice.amount_received),
        },
    )


@router.get("/", response_model=InvoicePage, name="invoice_list", dependencies=[StrictQuery])
async def list_invoices(
    session: SessionDep,
    caller: CurrentUser,
    entity_code: str | None = None,
    financial_year: str | None = None,
    status_: str | None = Query(None, alias="status"),
    outstanding: bool = False,
    search: str | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
) -> InvoicePage:
    """
    The register.

    🔴 An unknown query parameter is a 400, not a shrug. FastAPI ignores
    extras by default; `deps.reject_unknown_filters` reinstates the rule the
    Django service enforced by hand, because a filter that silently does
    nothing is how someone exports the whole register believing they exported
    one customer.
    """
    statement = select(Invoice).where(Invoice.is_deleted.is_(False))

    if entity_code:
        statement = statement.where(Invoice.entity_code == entity_code)
    if financial_year:
        statement = statement.where(Invoice.financial_year == financial_year)
    if status_:
        statement = statement.where(Invoice.status.in_([s.strip() for s in status_.split(",")]))
    if outstanding:
        statement = statement.where(Invoice.status.in_(OUTSTANDING_STATUSES))
    if search:
        term = f"%{search.strip()}%"
        statement = statement.where(
            or_(Invoice.invoice_no.ilike(term), Invoice.buyer_name.ilike(term))
        )

    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = await session.scalars(
        statement.order_by(Invoice.invoice_date.desc(), Invoice.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    return InvoicePage(count=total or 0, results=[_row(invoice) for invoice in rows])


@router.get(
    "/summary/", response_model=RegisterSummary, name="invoice_summary", dependencies=[StrictQuery]
)
async def summary(
    session: SessionDep,
    caller: CurrentUser,
    entity_code: str | None = None,
    financial_year: str | None = None,
) -> RegisterSummary:
    """
    Totals across whatever the register is currently filtered to.

    🔴 Cancelled, discarded and draft invoices are excluded from every figure.
    They are documents that exist and are not owed, and counting them makes a
    receivables number meaningless.
    """
    statement = select(Invoice).where(
        Invoice.is_deleted.is_(False),
        Invoice.status.notin_(NOT_OWED_STATUSES),
    )
    if entity_code:
        statement = statement.where(Invoice.entity_code == entity_code)
    if financial_year:
        statement = statement.where(Invoice.financial_year == financial_year)

    invoices = list(await session.scalars(statement))

    taxable = sum((i.taxable_value for i in invoices), Decimal(0))
    tax = sum((i.tax_amount for i in invoices), Decimal(0))
    total = sum((i.total_value for i in invoices), Decimal(0))
    received = sum((i.amount_received for i in invoices), Decimal(0))
    area = sum(
        (line.quantity_ha or Decimal(0) for i in invoices for line in i.lines),
        Decimal(0),
    )

    return RegisterSummary(
        count=len(invoices),
        taxable_value=taxable,
        tax_amount=tax,
        total_value=total,
        amount_received=received,
        amount_outstanding=total - received,
        total_area_ha=area,
        display=SummaryDisplay(
            taxable=format_inr(taxable),
            tax=format_inr(tax),
            total=format_inr(total),
            received=format_inr(received),
            outstanding=format_inr(total - received),
        ),
    )


# 🔴 Both path forms are registered, and neither redirects.
#
# FastAPI answers a trailing-slash mismatch with a 307 to an *absolute* URL on
# the backend origin. Behind the dev proxy that is a cross-origin redirect, and
# browsers strip `Authorization` across origins — so the retry arrived
# unauthenticated and the client saw 401, refreshed its token, and looped.
# The log read like an expiring session; it was a missing slash.
#
# The rest of this API uses trailing slashes (`/issue/`, `/cancel/`), so that
# form is canonical and the bare one is a hidden alias. `test_no_route_redirects`
# holds the rule for every route.
@router.get("/{invoice_id}/", response_model=InvoiceDetail, name="invoice_detail")
@router.get(
    "/{invoice_id}",
    response_model=InvoiceDetail,
    name="invoice_detail_alias",
    include_in_schema=False,
)
async def get_invoice(
    invoice_id: uuid.UUID, session: SessionDep, caller: CurrentUser
) -> InvoiceDetail:
    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")

    base = _row(invoice)
    return InvoiceDetail(
        **base.model_dump(),
        buyer_address=invoice.buyer_address,
        buyer_gstin=invoice.buyer_gstin,
        buyer_order_no=invoice.buyer_order_no,
        work_order_ref=invoice.work_order_ref,
        letter_ref=invoice.letter_ref,
        payment_terms=invoice.payment_terms,
        tax_rate_pct=invoice.tax_rate_pct,
        amount_in_words=invoice.amount_in_words,
        amount_received=invoice.amount_received,
        issued_at=invoice.issued_at,
        cancelled_at=invoice.cancelled_at,
        cancellation_reason=invoice.cancellation_reason,
        lines=[
            {
                "line_no": line.line_no,
                "description": line.description,
                "hsn_sac": line.hsn_sac,
                "quantity": line.quantity,
                "unit": line.unit,
                "quantity_ha": line.quantity_ha,
                "rate": line.rate,
                "line_taxable_value": line.line_taxable_value,
                "line_tax_amount": line.line_tax_amount,
                "line_total": line.line_total,
            }
            for line in invoice.lines
        ],
        payments=[
            {
                "amount": payment.amount,
                "amount_display": format_inr(payment.amount),
                "received_on": payment.received_on,
                "mode": payment.mode,
                "reference": payment.reference,
            }
            for payment in invoice.payments
        ],
    )
