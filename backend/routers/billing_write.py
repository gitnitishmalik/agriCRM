"""
Invoice writes: create, edit-while-draft, issue, cancel, record payment.

🔴 The state machine moves in one piece.

Issue and cancel are not CRUD. Issuing allocates a permanent number and freezes
a statutory document; cancelling burns that number rather than returning it to
the series. Both are guarded by the DDL as well as by this code — a trigger
refuses to change an allocated `invoice_no` (smoke test 18) and a CHECK refuses
a cancellation with no reason (smoke test 20).

Splitting the machine across two services during the migration was never an
option: an invoice issued by one and cancelled by the other would be correct
only by luck, and the failure would be a number handed out twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, text

from backend.deps import CurrentUser, SessionDep
from backend.domain.checks import gstin_check_evidence, run_checks
from backend.domain.hashing import matches
from backend.domain.scoping import BILLING_ISSUE, Scope
from backend.models.billing import BillingEntity, Invoice, InvoiceLine, InvoicePayment
from backend.models.invoice_ops import InvoiceCheckAck, InvoiceCheckRun
from backend.money import compute_line, money, rupees_in_words, sum_lines, to_hectares
from backend.schemas.billing import (
    CancelRequest,
    InvoiceCreate,
    InvoiceDetail,
    InvoiceUpdate,
    IssueResponse,
    PaymentRequest,
)
from backend.schemas.checks import IssueRequest

router = APIRouter(prefix="/api/v1/invoices", tags=["billing"])

#: Tax treatments where tax is separated out. The rest bill a gross amount and
#: show no tax — 🔴 which customers fall where is still open (INVOICE.md §5.4),
#: so nothing here infers it.
TAXABLE_TREATMENTS = frozenset({"igst", "cgst_sgst"})


def financial_year_of(when: date) -> str:
    """Indian FY: April to March. 12 Jun 2026 is 2026-27."""
    year = when.year if when.month >= 4 else when.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


async def _write_lines(session, invoice: Invoice, lines: list) -> None:
    """
    Replace the invoice's lines, computing every amount server-side.

    🔴 Amounts are never taken from the client. A caller that could post its
    own `line_total` could post an invoice whose lines do not sum to its
    header, and the document would be internally inconsistent in a way nobody
    notices until an accounts team rejects it.
    """
    taxable_supply = invoice.tax_treatment in TAXABLE_TREATMENTS

    computed = []
    for index, data in enumerate(lines, 1):
        amounts = compute_line(
            qty=data.quantity,
            rate=data.rate,
            tax_rate_pct=data.tax_rate_pct or invoice.tax_rate_pct,
            rate_is_tax_inclusive=data.rate_is_tax_inclusive,
            taxable_supply=taxable_supply,
        )
        computed.append(amounts)
        session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                line_no=data.line_no or index,
                description=data.description,
                hsn_sac=data.hsn_sac,
                quantity=data.quantity,
                unit=data.unit,
                rate=data.rate,
                rate_is_tax_inclusive=data.rate_is_tax_inclusive,
                line_taxable_value=amounts.taxable,
                line_tax_amount=amounts.tax,
                line_total=amounts.total,
                location_note=data.location_note,
            )
        )

    header = sum_lines(computed)
    invoice.taxable_value = header.taxable
    invoice.tax_amount = header.tax
    invoice.total_value = header.total
    invoice.amount_in_words = rupees_in_words(header.total)
    await session.flush()


async def _load(session, invoice_id: uuid.UUID) -> Invoice:
    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")
    return invoice


@router.post(
    "/", response_model=InvoiceDetail, status_code=status.HTTP_201_CREATED, name="invoice_create"
)
async def create_invoice(
    payload: InvoiceCreate, session: SessionDep, caller: CurrentUser
) -> InvoiceDetail:
    """Create a draft. A number is allocated at issue, never here."""
    entity = await session.scalar(
        select(BillingEntity).where(BillingEntity.id == payload.billing_entity)
    )
    if entity is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No such billing entity.")

    invoice = Invoice(
        billing_entity_id=entity.id,
        entity_code=entity.code,
        template_code=entity.template_code,
        invoice_date=payload.invoice_date,
        due_date=payload.due_date,
        buyer_name=payload.buyer_name,
        buyer_address=payload.buyer_address,
        buyer_gstin=payload.buyer_gstin,
        buyer_state_code=payload.buyer_state_code,
        buyer_is_govt_uin=payload.buyer_is_govt_uin,
        buyer_order_no=payload.buyer_order_no,
        work_order_ref=payload.work_order_ref,
        letter_ref=payload.letter_ref,
        payment_terms=payload.payment_terms,
        organisation_id=payload.organisation,
        tax_treatment=payload.tax_treatment,
        tax_rate_pct=payload.tax_rate_pct,
        place_of_supply_state_code=payload.place_of_supply_state_code,
        status="draft",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        created_by=caller.user.public_id,
        updated_by=caller.user.public_id,
    )
    session.add(invoice)
    await session.flush()

    await _write_lines(session, invoice, payload.lines)
    await session.refresh(invoice)
    return await _detail(invoice)


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
@router.put("/{invoice_id}/", response_model=InvoiceDetail, name="invoice_replace")
@router.patch("/{invoice_id}/", response_model=InvoiceDetail, name="invoice_update")
@router.put(
    "/{invoice_id}",
    response_model=InvoiceDetail,
    name="invoice_replace_alias",
    include_in_schema=False,
)
@router.patch(
    "/{invoice_id}",
    response_model=InvoiceDetail,
    name="invoice_update_alias",
    include_in_schema=False,
)
async def update_invoice(
    invoice_id: uuid.UUID, payload: InvoiceUpdate, session: SessionDep, caller: CurrentUser
) -> InvoiceDetail:
    """
    Edit a draft.

    🔴 Only a draft. Once issued the document exists in someone else's
    accounts, and changing it there is not an edit — it is a different
    document wearing the same number.
    """
    invoice = await _load(session, invoice_id)
    if invoice.status != "draft":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Only a draft can be edited; this one is {invoice.status}. "
            "Cancel it and raise a new invoice.",
        )

    data = payload.model_dump(exclude_unset=True, exclude={"lines"})
    for field, value in data.items():
        setattr(invoice, field, value)
    invoice.updated_at = datetime.now(UTC)
    invoice.updated_by = caller.user.public_id
    # 🔴 Flush before the refresh below, not after. `session.refresh()` expires
    # the instance and re-reads it, so an unflushed attribute change is thrown
    # away — and the endpoint answers 200 having saved nothing. Line edits
    # survived only because `_write_lines` flushes on its way past; a
    # header-only PATCH silently did nothing at all.
    await session.flush()

    if payload.lines is not None:
        for line in list(invoice.lines):
            await session.delete(line)
        await session.flush()
        await _write_lines(session, invoice, payload.lines)

    await session.refresh(invoice)
    return await _detail(invoice)


@router.post("/{invoice_id}/issue/", response_model=IssueResponse, name="invoice_issue")
async def issue_invoice(
    invoice_id: uuid.UUID,
    session: SessionDep,
    caller: CurrentUser,
    scope: Scope,
    payload: IssueRequest | None = None,
) -> IssueResponse:
    """
    Allocate a number and freeze the document. The point of no return.

    🔴 The allocation takes a row lock on the series. Two people issuing at the
    same moment is exactly when a series hands out a duplicate — the unique
    index would then reject the second insert, which is correct but arrives as
    a 500 rather than as a queue.

    🔴 **The pre-issue checks run here**, not only on the confirmation screen.
    A client that never calls `/checks/`, or calls it and ignores the answer,
    still cannot issue an invoice with a malformed GSTIN or a cancelled
    registration — because this endpoint runs the same checks itself and
    refuses on its own findings. A control that depends on a UI remembering to
    ask is not a control.
    """
    invoice = await _load(session, invoice_id)
    scope.check(invoice.billing_entity_id, what="invoice")
    scope.require(BILLING_ISSUE, "issue an invoice")

    if invoice.status != "draft":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Only a draft can be issued; this one is {invoice.status}.",
        )
    if not invoice.lines:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "An invoice needs at least one line before it can be issued.",
        )

    request = payload or IssueRequest()

    # Acknowledgements from the confirmation screen, recorded before the
    # checks run so an accepted warning counts on this pass.
    for item in request.acknowledge:
        already = await session.scalar(
            select(InvoiceCheckAck).where(
                InvoiceCheckAck.invoice_id == invoice.id,
                InvoiceCheckAck.check_code == item.code,
                InvoiceCheckAck.acknowledged_by == caller.user.public_id,
            )
        )
        if already is None:
            session.add(
                InvoiceCheckAck(
                    invoice_id=invoice.id,
                    check_code=item.code,
                    severity="warning",
                    reason=item.reason.strip(),
                    acknowledged_by=caller.user.public_id,
                    acknowledged_at=datetime.now(UTC),
                )
            )
    if request.acknowledge:
        await session.flush()

    report = await run_checks(session, invoice)

    # A hash the caller quoted from an earlier check run must still describe
    # this draft. If it does not, the invoice was edited after the screen was
    # rendered and the person is confirming something they did not see.
    if request.invoice_sha256 and not matches(report.invoice_sha256, request.invoice_sha256):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": (
                    "This draft changed after the checks you are confirming. "
                    "Re-run the checks and review the current state before issuing."
                ),
                "details": {"current_invoice_sha256": report.invoice_sha256.hex()},
            },
        )

    if report.blocking:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": (
                    f"{len(report.blocking)} check(s) block issue. An invoice with "
                    f"these problems would be wrong in someone else's accounts."
                ),
                "details": {
                    "blocking": [result.as_dict() for result in report.blocking],
                    "invoice_sha256": report.invoice_sha256.hex(),
                },
            },
        )

    financial_year = financial_year_of(invoice.invoice_date)
    number = await _allocate_number(session, invoice.entity_code, financial_year)

    invoice.financial_year = financial_year
    invoice.invoice_no = number
    invoice.status = "issued"
    invoice.issued_at = datetime.now(UTC)
    invoice.updated_at = datetime.now(UTC)
    invoice.updated_by = caller.user.public_id
    await session.flush()

    # 🔴 Freeze what was known at issue. Both rows are immutable afterwards:
    # re-verifying a GSTIN next year writes a new verification and must not
    # rewrite what this document was checked against.
    session.add(
        InvoiceCheckRun(
            invoice_id=invoice.id,
            ran_at=datetime.now(UTC),
            ran_by=caller.user.public_id,
            invoice_sha256=report.invoice_sha256,
            blocking_count=0,
            warning_count=len(report.warnings),
            results=[result.as_dict() for result in report.results],
            is_issue_evidence=True,
        )
    )
    await gstin_check_evidence(session, invoice, report, actor=caller.user.public_id)
    await session.flush()

    return IssueResponse(
        invoice_no=number,
        status=invoice.status,
        warnings=[result.as_dict() for result in report.warnings],
    )


async def _allocate_number(session, entity_code: str, financial_year: str) -> str:
    """
    Take the next number and advance the series. Never reuses.

    `FOR UPDATE` rather than an optimistic read: a cancelled invoice keeps its
    number and the series has already moved past it, so handing the same one
    out twice produces two documents claiming to be the same invoice.
    """
    row = (
        await session.execute(
            text(
                "SELECT id, pattern, next_number FROM crm.invoice_number_series "
                "WHERE entity_code = :entity AND financial_year = :fy AND stream = '' "
                "FOR UPDATE"
            ),
            {"entity": entity_code, "fy": financial_year},
        )
    ).first()

    if row is None:
        await session.execute(
            text(
                "INSERT INTO crm.invoice_number_series "
                "(id, entity_code, financial_year, stream, pattern, next_number) "
                "VALUES (gen_random_uuid(), :entity, :fy, '', "
                "'{entity}/{fy}/{stream}{n}', 1)"
            ),
            {"entity": entity_code, "fy": financial_year},
        )
        pattern, number = "{entity}/{fy}/{stream}{n}", 1
    else:
        # The id is not needed — the UPDATE below matches on the same three
        # columns the SELECT ... FOR UPDATE locked.
        _, pattern, number = row

    await session.execute(
        text(
            "UPDATE crm.invoice_number_series SET next_number = :next "
            "WHERE entity_code = :entity AND financial_year = :fy AND stream = ''"
        ),
        {"next": number + 1, "entity": entity_code, "fy": financial_year},
    )

    return pattern.format(entity=entity_code, fy=financial_year, stream="", n=number)


@router.post("/{invoice_id}/cancel/", name="invoice_cancel")
async def cancel_invoice(
    invoice_id: uuid.UUID, payload: CancelRequest, session: SessionDep, caller: CurrentUser
) -> dict[str, str | None]:
    """
    Cancel, keeping the number.

    🔴 The number is burned, not returned to the series. A reason is required
    here and by a database CHECK, because that is the field left blank
    throughout the historical data.
    """
    invoice = await _load(session, invoice_id)

    if not payload.reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A cancellation needs a reason.")
    if invoice.status in ("draft", "discarded"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A draft is discarded, not cancelled — it never became a document.",
        )

    invoice.status = "cancelled"
    invoice.cancelled_at = datetime.now(UTC)
    invoice.cancellation_reason = payload.reason.strip()
    invoice.updated_at = datetime.now(UTC)
    invoice.updated_by = caller.user.public_id
    await session.flush()

    return {"status": invoice.status, "invoice_no": invoice.invoice_no}


@router.post("/{invoice_id}/payments/", response_model=InvoiceDetail, name="invoice_payment")
async def record_payment(
    invoice_id: uuid.UUID, payload: PaymentRequest, session: SessionDep, caller: CurrentUser
) -> InvoiceDetail:
    """
    Record money received.

    Status follows the money — the database trigger moves the invoice to
    `part_paid` or `paid` (smoke test 20), so this does not set it. Two places
    deciding what "paid" means is how a register stops agreeing with itself.
    """
    invoice = await _load(session, invoice_id)

    if invoice.status in ("draft", "cancelled", "discarded"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Cannot record a payment against a {invoice.status} invoice.",
        )
    if payload.amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A payment must be positive.")

    session.add(
        InvoicePayment(
            invoice_id=invoice.id,
            amount=money(payload.amount),
            received_on=payload.received_on,
            mode=payload.mode,
            reference=payload.reference,
            note=payload.note,
            recorded_by=caller.user.public_id,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    await session.refresh(invoice)
    return await _detail(invoice)


@router.delete("/{invoice_id}/", status_code=status.HTTP_204_NO_CONTENT, name="invoice_delete")
@router.delete(
    "/{invoice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    name="invoice_delete_alias",
    include_in_schema=False,
)
async def discard_draft(invoice_id: uuid.UUID, session: SessionDep, caller: CurrentUser) -> None:
    """
    🔴 Only a draft. An issued invoice is cancelled, with a reason, and keeps
    its number — a document that exists in someone's accounts cannot be made
    not to have existed.
    """
    invoice = await _load(session, invoice_id)
    if invoice.status != "draft":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only a draft can be deleted. An issued invoice is cancelled, with a "
            "reason, and keeps its number.",
        )
    invoice.is_deleted = True
    invoice.status = "discarded"
    invoice.updated_at = datetime.now(UTC)
    invoice.updated_by = caller.user.public_id
    await session.flush()


async def _detail(invoice: Invoice) -> InvoiceDetail:
    """Shared with the read router, so one shape is served either way."""
    from backend.money import format_inr
    from backend.routers.billing import _row

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
                "quantity_ha": line.quantity_ha or to_hectares(line.quantity, line.unit),
                "rate": line.rate,
                "line_taxable_value": line.line_taxable_value,
                "line_tax_amount": line.line_tax_amount,
                "line_total": line.line_total,
            }
            for line in invoice.lines
        ],
        payments=[
            {
                "amount": p.amount,
                # 🔴 The second place a payment row is built. `billing.py` has the
                # other one, and they have to agree — this one was missed when
                # `amount_display` was added and the write path 500'd on its own
                # response model until a test caught it.
                "amount_display": format_inr(p.amount),
                "received_on": p.received_on,
                "mode": p.mode,
                "reference": p.reference,
            }
            for p in invoice.payments
        ],
    )
