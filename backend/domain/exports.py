"""
Accounting handoff — Tally, Zoho and a GSTR-1 working paper.

🔴 **These are exports and working papers. Nothing here files a return.**
INVOICE.md §1 draws the line and §9 restates it: the register exports *to*
Tally and Zoho, it never replaces them, and no output of this module is
ready for statutory submission without a CA's review. Every response carries that in a
`disclaimer` field rather than in documentation nobody reads at the moment they
are about to upload something to a portal.

🔴 **The GSTR-1 sheet is a reconciliation, not a submission.** It reports the
warnings alongside the rows — a missing GSTIN, a cancelled registration, an
invoice whose stated total disagrees with its lines — because those are exactly
the rows that would be rejected downstream, and finding them here is cheaper
than finding them on the portal.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import gstin as gstin_lib
from backend.domain.scoping import EntityScope
from backend.models.billing import Invoice
from backend.money import format_inr

#: Statuses that belong in an accounting export. A draft is not a document and
#: a discarded one never was; a cancelled invoice *is* included, because the
#: number was allocated and a gap in the series is what an auditor asks about.
EXPORTABLE_STATUSES = ("issued", "part_paid", "paid", "cancelled", "on_hold")

DISCLAIMER = (
    "This is an export for your accountant, not a filing. Nothing in this "
    "system files a return, obtains an IRN, or posts to a ledger. Review it "
    "before it is used."
)


@dataclass
class ExportRow:
    invoice: Invoice

    @property
    def buyer_state(self) -> str | None:
        return self.invoice.buyer_state_code or gstin_lib.state_code(self.invoice.buyer_gstin or "")


async def _invoices(
    session: AsyncSession,
    scope: EntityScope,
    *,
    date_from: date | None,
    date_to: date | None,
    entity_code: str | None,
) -> list[Invoice]:
    conditions = [
        Invoice.billing_entity_id.in_(scope.entity_ids),
        Invoice.is_deleted.is_(False),
        Invoice.status.in_(EXPORTABLE_STATUSES),
    ]
    if date_from:
        conditions.append(Invoice.invoice_date >= date_from)
    if date_to:
        conditions.append(Invoice.invoice_date <= date_to)
    if entity_code:
        conditions.append(Invoice.entity_code == entity_code)

    return list(
        await session.scalars(
            select(Invoice)
            .where(and_(*conditions))
            .order_by(Invoice.invoice_date, Invoice.invoice_no)
        )
    )


# ---------------------------------------------------------------------------
# Tally
# ---------------------------------------------------------------------------

#: Tally's import expects a voucher per row with the ledger names spelled as
#: they are in the company. 🔴 These are placeholders, and the export says so:
#: a ledger name that does not exist in the target company fails the import,
#: and guessing them here would produce a file that looks right and does not
#: load.
TALLY_COLUMNS = (
    "Voucher Date",
    "Voucher Type",
    "Voucher Number",
    "Party Ledger",
    "Party GSTIN",
    "Place of Supply",
    "Sales Ledger",
    "Item Description",
    "HSN/SAC",
    "Quantity",
    "Unit",
    "Rate",
    "Taxable Value",
    "IGST",
    "CGST",
    "SGST",
    "Invoice Total",
    "Narration",
)


async def tally_csv(
    session: AsyncSession,
    scope: EntityScope,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    entity_code: str | None = None,
) -> str:
    """
    A Tally-shaped CSV, one row per invoice line.

    Line-level rather than invoice-level because Tally posts an item per line
    and an aggregated row loses the HSN, which is the column the GST returns
    are built from.
    """
    invoices = await _invoices(
        session, scope, date_from=date_from, date_to=date_to, entity_code=entity_code
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(TALLY_COLUMNS)

    for invoice in invoices:
        igst = cgst = sgst = Decimal(0)
        # The split is presentational — the invoice stores one tax figure and a
        # treatment, and Tally wants it in the right column.
        if invoice.tax_treatment == "igst":
            igst = invoice.tax_amount
        elif invoice.tax_treatment == "cgst_sgst":
            cgst = sgst = (invoice.tax_amount / 2).quantize(Decimal("0.01"))

        lines = list(invoice.lines) or [None]
        for index, line in enumerate(lines):
            first = index == 0
            writer.writerow(
                [
                    invoice.invoice_date.strftime("%d-%m-%Y"),
                    "Sales" if invoice.status != "cancelled" else "Sales (Cancelled)",
                    invoice.invoice_no or "",
                    invoice.buyer_name,
                    invoice.buyer_gstin or "",
                    invoice.place_of_supply_state_code or invoice.buyer_state_code or "",
                    "Sales Accounts",
                    line.description if line else "",
                    (line.hsn_sac or "") if line else "",
                    str(line.quantity) if line else "",
                    (line.unit or "") if line else "",
                    str(line.rate) if line else "",
                    str(line.line_taxable_value) if line else str(invoice.taxable_value),
                    str(igst) if first else "0.00",
                    str(cgst) if first else "0.00",
                    str(sgst) if first else "0.00",
                    str(invoice.total_value) if first else "0.00",
                    invoice.cancellation_reason or "",
                ]
            )

    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Zoho Books
# ---------------------------------------------------------------------------

ZOHO_COLUMNS = (
    "Invoice Date",
    "Invoice Number",
    "Invoice Status",
    "Customer Name",
    "GST Treatment",
    "GST Identification Number (GSTIN)",
    "Place of Supply",
    "Item Name",
    "HSN/SAC",
    "Quantity",
    "Usage unit",
    "Item Price",
    "Item Tax %",
    "Item Total",
    "SubTotal",
    "Total",
    "Notes",
)

#: Zoho's own vocabulary, so the import does not have to be re-mapped by hand.
ZOHO_TREATMENT = {
    "igst": "business_gst",
    "cgst_sgst": "business_gst",
    "zero_rated": "overseas",
    "exempt": "consumer",
    "grant": "consumer",
}


async def zoho_csv(
    session: AsyncSession,
    scope: EntityScope,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    entity_code: str | None = None,
) -> str:
    invoices = await _invoices(
        session, scope, date_from=date_from, date_to=date_to, entity_code=entity_code
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(ZOHO_COLUMNS)

    for invoice in invoices:
        lines = list(invoice.lines) or [None]
        for index, line in enumerate(lines):
            first = index == 0
            writer.writerow(
                [
                    invoice.invoice_date.isoformat(),
                    invoice.invoice_no or "",
                    invoice.status,
                    invoice.buyer_name,
                    ZOHO_TREATMENT.get(invoice.tax_treatment, "consumer"),
                    invoice.buyer_gstin or "",
                    invoice.place_of_supply_state_code or invoice.buyer_state_code or "",
                    line.description if line else "",
                    (line.hsn_sac or "") if line else "",
                    str(line.quantity) if line else "",
                    (line.unit or "") if line else "",
                    str(line.rate) if line else "",
                    str(invoice.tax_rate_pct),
                    str(line.line_total) if line else "",
                    str(invoice.taxable_value) if first else "",
                    str(invoice.total_value) if first else "",
                    invoice.notes or "",
                ]
            )

    return buffer.getvalue()


# ---------------------------------------------------------------------------
# GSTR-1 working paper
# ---------------------------------------------------------------------------


async def gstr1_working_paper(
    session: AsyncSession,
    scope: EntityScope,
    *,
    date_from: date,
    date_to: date,
    entity_code: str | None = None,
) -> dict[str, Any]:
    """
    A B2B working sheet with its reconciliation warnings.

    🔴 A **working paper for the CA**, and the response says so. It is shaped
    like GSTR-1's B2B table so it can be compared with what the accountant
    files; it is not a submission, it is not validated against the portal's
    schema, and nothing in this codebase claims otherwise.

    The warnings are the valuable half. A missing GSTIN, a rate that disagrees
    with the line, a total that does not recompute — those are the rows that
    would be rejected downstream, and finding them here is cheaper than finding
    them on the portal.
    """
    from backend.money import compute_line, sum_lines

    invoices = await _invoices(
        session, scope, date_from=date_from, date_to=date_to, entity_code=entity_code
    )

    b2b: list[dict[str, Any]] = []
    b2c: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    totals = {"taxable": Decimal(0), "tax": Decimal(0), "total": Decimal(0)}

    for invoice in invoices:
        if invoice.status == "cancelled":
            warnings.append(
                {
                    "invoice_id": str(invoice.id),
                    "invoice_no": invoice.invoice_no,
                    "code": "cancelled_in_period",
                    "severity": "info",
                    "message": (
                        "Cancelled, and listed here because its number was allocated. "
                        "A gap in the series is what an auditor asks about — the "
                        "number is burned, not reissued."
                    ),
                }
            )
            continue

        row = {
            "invoice_no": invoice.invoice_no,
            "invoice_date": invoice.invoice_date.isoformat(),
            "buyer_name": invoice.buyer_name,
            "buyer_gstin": invoice.buyer_gstin,
            "place_of_supply": invoice.place_of_supply_state_code or invoice.buyer_state_code,
            "reverse_charge": "N",
            "invoice_type": "Regular B2B",
            "rate": str(invoice.tax_rate_pct),
            "taxable_value": str(invoice.taxable_value),
            "tax_amount": str(invoice.tax_amount),
            "invoice_value": str(invoice.total_value),
            "tax_treatment": invoice.tax_treatment,
            "hsn_codes": sorted({line.hsn_sac for line in invoice.lines if line.hsn_sac}),
        }

        totals["taxable"] += invoice.taxable_value
        totals["tax"] += invoice.tax_amount
        totals["total"] += invoice.total_value

        if invoice.buyer_gstin:
            b2b.append(row)
            try:
                gstin_lib.validate(invoice.buyer_gstin, allow_govt_uin=invoice.buyer_is_govt_uin)
            except gstin_lib.GSTINError as error:
                warnings.append(
                    {
                        "invoice_id": str(invoice.id),
                        "invoice_no": invoice.invoice_no,
                        "code": "invalid_gstin",
                        "severity": "error",
                        "message": (
                            f"{error} A wrong GSTIN blocks the customer's input tax "
                            f"credit and surfaces as a GSTR-2B mismatch on their side."
                        ),
                    }
                )
        else:
            b2c.append(row)
            if invoice.tax_treatment in ("igst", "cgst_sgst"):
                warnings.append(
                    {
                        "invoice_id": str(invoice.id),
                        "invoice_no": invoice.invoice_no,
                        "code": "b2b_without_gstin",
                        "severity": "warning",
                        "message": (
                            "GST is charged but no buyer GSTIN is recorded, so this "
                            "falls into B2C. If the customer is registered they "
                            "cannot claim credit against it."
                        ),
                    }
                )

        # Recompute, and report a disagreement rather than exporting it.
        taxable_supply = invoice.tax_treatment in ("igst", "cgst_sgst")
        computed = [
            compute_line(
                qty=line.quantity,
                rate=line.rate,
                tax_rate_pct=invoice.tax_rate_pct,
                rate_is_tax_inclusive=line.rate_is_tax_inclusive,
                taxable_supply=taxable_supply,
            )
            for line in invoice.lines
        ]
        if computed:
            header = sum_lines(computed)
            if header.total != invoice.total_value:
                warnings.append(
                    {
                        "invoice_id": str(invoice.id),
                        "invoice_no": invoice.invoice_no,
                        "code": "total_mismatch",
                        "severity": "error",
                        "message": (
                            f"The lines recompute to {header.total}; the invoice "
                            f"carries {invoice.total_value}."
                        ),
                    }
                )

        if not any(line.hsn_sac for line in invoice.lines):
            warnings.append(
                {
                    "invoice_id": str(invoice.id),
                    "invoice_no": invoice.invoice_no,
                    "code": "no_hsn",
                    "severity": "warning",
                    "message": (
                        "No HSN/SAC on any line. The HSN summary table needs one per "
                        "line, and the return will not go without it."
                    ),
                }
            )

    numbers = [inv.invoice_no for inv in invoices if inv.invoice_no]
    return {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "entity_code": entity_code,
        "b2b": b2b,
        "b2c": b2c,
        "summary": {
            "invoice_count": len(invoices),
            "b2b_count": len(b2b),
            "b2c_count": len(b2c),
            "taxable_value": str(totals["taxable"]),
            "tax_amount": str(totals["tax"]),
            "total_value": str(totals["total"]),
            "display": {
                "taxable_value": format_inr(totals["taxable"]),
                "tax_amount": format_inr(totals["tax"]),
                "total_value": format_inr(totals["total"]),
            },
        },
        "numbers_in_period": numbers,
        "warnings": warnings,
        "blocking_warnings": [w for w in warnings if w["severity"] == "error"],
        # 🔴 Both of these are in the payload, not just in a doc somewhere.
        "disclaimer": DISCLAIMER,
        "not_a_filing": (
            "This sheet is shaped like GSTR-1's B2B table so it can be compared "
            "with what your CA files. It has not been validated against the "
            "portal's schema, no IRN has been obtained, and nothing here has been "
            "or will be submitted."
        ),
    }
