"""
The invoice as a document: HTML, PDF and a live preview.

`preview` is what makes the create screen work — every keystroke can redraw the
actual document rather than an approximation of it, because it is the same
template and the same arithmetic the issued PDF will use. An approximation that
diverges from the issued document is worse than no preview at all.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from backend.deps import CurrentUser, SessionDep
from backend.models.billing import BillingEntity, Invoice
from backend.money import format_inr, to_hectares
from backend.render import render_html
from backend.routers.billing_write import financial_year_of
from backend.schemas.billing import PreviewRequest

router = APIRouter(prefix="/api/v1/invoices", tags=["billing"])


@router.get("/{invoice_id}/html/", name="invoice_html")
@router.get("/{invoice_id}/html", name="invoice_html_alias", include_in_schema=False)
async def invoice_html(invoice_id: uuid.UUID, session: SessionDep, caller: CurrentUser) -> Response:
    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")

    return Response(
        content=render_html(invoice, entity=invoice.billing_entity, lines=invoice.lines),
        media_type="text/html; charset=utf-8",
    )


@router.get("/{invoice_id}/pdf/", name="invoice_pdf")
@router.get("/{invoice_id}/pdf", name="invoice_pdf_alias", include_in_schema=False)
async def invoice_pdf(invoice_id: uuid.UUID, session: SessionDep, caller: CurrentUser) -> Response:
    """
    The document as PDF.

    🔴 Rendered from the same HTML the `/html` route serves, never from a
    second layout. Two renderers is two documents, and the one the customer
    receives would not be the one anybody reviewed.
    """
    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")

    html = render_html(invoice, entity=invoice.billing_entity, lines=invoice.lines)

    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        # An honest 501 rather than a 500. The renderer is an optional
        # dependency with system libraries behind it; saying so tells the
        # caller what to install instead of what crashed.
        #
        # 🔴 `OSError`, not just `ImportError`. WeasyPrint is a *Python*
        # package that loads GTK through cffi at import time, so on a machine
        # without those libraries — every Windows box, and any slim container
        # — `pip install weasyprint` succeeds and the import then raises
        # `OSError: cannot load library 'libgobject-2.0-0'`. Catching only
        # ImportError turned "no PDF engine here" into a 500.
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            f"PDF rendering is not available on this server: {error}. "
            "WeasyPrint needs its GTK system libraries, which Windows and slim "
            "container images do not ship. The same document is available at "
            "/html, which needs nothing.",
        ) from error

    return Response(
        content=HTML(string=html).write_pdf(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'inline; filename="{(invoice.invoice_no or "draft").replace("/", "-")}.pdf"'
            )
        },
    )


@router.post("/preview/", name="invoice_preview")
async def preview(payload: PreviewRequest, session: SessionDep, caller: CurrentUser) -> Response:
    """
    Render an invoice that does not exist yet.

    Builds a detached `Invoice` — never added to the session — so a preview
    cannot leave a row behind. That matters: the create screen calls this on
    every keystroke.
    """
    from datetime import UTC, datetime
    from decimal import Decimal

    from backend.models.billing import InvoiceLine
    from backend.money import compute_line, rupees_in_words, sum_lines

    # 🔴 Either identifier. The create screen binds a dropdown to `entity_code`
    # ("TEPL"); the register carries the row's UUID. Requiring only the UUID
    # meant the live preview 400'd on every keystroke.
    if payload.billing_entity is not None:
        entity = await session.scalar(
            select(BillingEntity).where(BillingEntity.id == payload.billing_entity)
        )
    elif payload.entity_code:
        entity = await session.scalar(
            select(BillingEntity).where(
                BillingEntity.code == payload.entity_code.upper(),
                BillingEntity.valid_to.is_(None),
            )
        )
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Name the issuing company: send `billing_entity` (its id) or "
            "`entity_code` (TFD / TEPL).",
        )

    if entity is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No such billing entity.")

    invoice = Invoice(
        id=uuid.uuid4(),
        billing_entity_id=entity.id,
        entity_code=entity.code,
        template_code=entity.template_code,
        invoice_date=payload.invoice_date,
        buyer_name=payload.buyer_name,
        buyer_address=payload.buyer_address,
        buyer_gstin=payload.buyer_gstin,
        buyer_state_code=payload.buyer_state_code,
        tax_treatment=payload.tax_treatment,
        tax_rate_pct=payload.tax_rate_pct,
        status="draft",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    taxable = invoice.tax_treatment in ("igst", "cgst_sgst")
    lines, computed = [], []
    for index, data in enumerate(payload.lines, 1):
        amounts = compute_line(
            # `or 0` because a preview line may be half-typed. This path
            # renders and saves nothing, so a blank figure is a blank cell on
            # the document rather than an error.
            qty=data.quantity or Decimal(0),
            rate=data.rate or Decimal(0),
            tax_rate_pct=data.tax_rate_pct or payload.tax_rate_pct,
            rate_is_tax_inclusive=data.rate_is_tax_inclusive,
            taxable_supply=taxable,
        )
        computed.append(amounts)
        lines.append(
            InvoiceLine(
                line_no=data.line_no or index,
                description=data.description,
                hsn_sac=data.hsn_sac,
                quantity=data.quantity or Decimal(0),
                unit=data.unit,
                rate=data.rate or Decimal(0),
                line_taxable_value=amounts.taxable,
                line_tax_amount=amounts.tax,
                line_total=amounts.total,
            )
        )

    header = sum_lines(computed) if computed else None
    invoice.taxable_value = header.taxable if header else Decimal(0)
    invoice.tax_amount = header.tax if header else Decimal(0)
    invoice.total_value = header.total if header else Decimal(0)
    invoice.amount_in_words = rupees_in_words(invoice.total_value)

    total_area_ha = sum(
        (to_hectares(line.quantity, line.unit) or Decimal(0) for line in lines),
        Decimal(0),
    )

    # 🔴 JSON with the document inside it, not a bare `text/html` body.
    #
    # The create screen renders the markup into an iframe *and* shows the
    # figures beside it, so it needs both — and it reads `result.html`. A raw
    # HTML response left that undefined: the pane stayed blank while the
    # request answered 200, which is the failure mode that looks like a
    # rendering bug and is a contract mismatch.
    #
    # The figures are pre-formatted here for the same reason they are
    # everywhere else: Indian grouping lives in `money.py`, and a second
    # implementation in TypeScript is a second one to get wrong.
    return {
        "html": render_html(invoice, entity=entity, lines=lines),
        "taxable_value": str(invoice.taxable_value),
        "tax_amount": str(invoice.tax_amount),
        "total_value": str(invoice.total_value),
        "amount_in_words": invoice.amount_in_words or "",
        "total_area_ha": str(total_area_ha),
        # What this invoice *would* be numbered. 🔴 Reading it allocates
        # nothing — the number is taken inside the issue transaction, and a
        # preview that consumed one would burn a number per keystroke.
        "next_invoice_no": f"{entity.code}/{financial_year_of(payload.invoice_date)}/…",
        "pdf_backend": _pdf_backend(),
        "display": {
            "taxable": format_inr(invoice.taxable_value),
            "tax": format_inr(invoice.tax_amount),
            "total": format_inr(invoice.total_value),
        },
    }


def _pdf_backend() -> str | None:
    """
    Which PDF engine is available, or None. The UI greys out its button.

    🔴 Catches `OSError` as well as `ImportError`, and for the same reason the
    route above does: WeasyPrint installs cleanly and then fails to *import*
    on any machine without GTK. A probe that only caught ImportError reported
    "engine present" right up until the first render, and crashed the preview
    on a machine where the button should simply have been grey.
    """
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        return None
    return "weasyprint"
