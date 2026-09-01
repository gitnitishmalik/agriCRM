"""
Pre-issue checks — deterministic, evidenced, and not negotiable by a model.

INVOICE.md §12.3 E and the `CLAUDE_INVOICE_BUILD_PROMPT` §3 list what must be
checked before an invoice becomes a document. This module is that list, run in
one pass, returning structured results a UI can display and an issue endpoint
can refuse on.

Three design rules, each with a failure it prevents:

🔴 **A check never invents data.** Where the evidence does not exist yet —
project operation logs, geospatial areas — the check returns `not_available`
with a reason. It does not skip silently (which reads as a pass) and it does
not estimate (which reads as a fact). `crm.project` lands in Phase 3; until
then the area reconciliation says so in as many words.

🔴 **Severity is decided here, not by the caller and not by a model.** The
copilot may explain a check and may not remove, reorder or downgrade one. The
result objects are plain data with no mutation path, and `run_checks` is
called by the issue endpoint itself rather than trusted from a request.

🔴 **Blocking is a property of the finding, not of the UI.** A frontend that
forgets to grey out a button must not be able to issue an invoice with a
malformed GSTIN, so `issue_invoice` re-runs this and refuses on its own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import gstin as gstin_lib
from backend.domain.hashing import sha256_of
from backend.models.billing import Invoice, InvoiceLine
from backend.models.business import Organisation
from backend.models.invoice_ops import (
    GSTIN_OK_STATUSES,
    ContractRate,
    GstinVerification,
    InvoiceCheckAck,
    InvoiceGstinCheck,
)
from backend.money import compute_line, sum_lines

Severity = Literal["info", "warning", "error"]

#: Treatments where tax is separated out. 🔴 Which customers fall where is
#: still open (INVOICE.md §5.4) — nothing in this module infers it, and the
#: `tax_treatment_unset` check exists to keep the question visible rather than
#: to answer it.
TAXABLE_TREATMENTS = frozenset({"igst", "cgst_sgst"})


@dataclass(frozen=True)
class CheckResult:
    """
    One finding.

    `code` is stable and machine-readable — acknowledgements key on it, so
    renaming one silently un-acknowledges every invoice that carried it.
    `explanation` is written for the person about to issue the document.
    """

    code: str
    severity: Severity
    title: str
    explanation: str
    blocks_issue: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)
    #: True when the check could not run for want of data that does not exist
    #: yet. Rendered distinctly: "not checked" is not "checked and fine".
    not_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "explanation": self.explanation,
            "blocks_issue": self.blocks_issue,
            "evidence": self.evidence,
            "not_available": self.not_available,
        }


@dataclass
class CheckReport:
    invoice_id: uuid.UUID
    invoice_sha256: bytes
    results: list[CheckResult]
    acknowledged_codes: frozenset[str] = frozenset()

    @property
    def blocking(self) -> list[CheckResult]:
        return [r for r in self.results if r.blocks_issue]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.severity == "warning" and not r.blocks_issue]

    @property
    def unacknowledged_warnings(self) -> list[CheckResult]:
        return [r for r in self.warnings if r.code not in self.acknowledged_codes]

    @property
    def can_issue(self) -> bool:
        return not self.blocking

    def as_dict(self) -> dict[str, Any]:
        return {
            "invoice_id": str(self.invoice_id),
            "invoice_sha256": self.invoice_sha256.hex(),
            "can_issue": self.can_issue,
            "blocking_count": len(self.blocking),
            "warning_count": len(self.warnings),
            "unacknowledged_warning_count": len(self.unacknowledged_warnings),
            "acknowledged_codes": sorted(self.acknowledged_codes),
            "results": [r.as_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# The invoice fingerprint
# ---------------------------------------------------------------------------


def fingerprint(invoice: Invoice, lines: list[InvoiceLine]) -> bytes:
    """
    A hash of everything a check could care about.

    🔴 This is what stops a clean check run being reused after an edit. A
    fingerprint over the whole row would change when `updated_at` moved and
    force a re-check for nothing; one over only the totals would miss a
    changed buyer GSTIN. The field list is therefore explicit, and adding a
    checked field means adding it here too.
    """
    return sha256_of(
        {
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "entity_code": invoice.entity_code,
            "template_code": invoice.template_code,
            "organisation_id": invoice.organisation_id,
            "buyer_name": invoice.buyer_name,
            "buyer_gstin": invoice.buyer_gstin,
            "buyer_state_code": invoice.buyer_state_code,
            "buyer_is_govt_uin": invoice.buyer_is_govt_uin,
            "buyer_order_no": invoice.buyer_order_no,
            "tax_treatment": invoice.tax_treatment,
            "tax_rate_pct": invoice.tax_rate_pct,
            "taxable_value": invoice.taxable_value,
            "tax_amount": invoice.tax_amount,
            "total_value": invoice.total_value,
            "consignee_name": invoice.consignee_name,
            "work_order_ref": invoice.work_order_ref,
            "letter_ref": invoice.letter_ref,
            "data_link_url": invoice.data_link_url,
            "lines": [
                {
                    "line_no": line.line_no,
                    "description": line.description,
                    "hsn_sac": line.hsn_sac,
                    "quantity": line.quantity,
                    "unit": line.unit,
                    "rate": line.rate,
                    "rate_is_tax_inclusive": line.rate_is_tax_inclusive,
                    "line_taxable_value": line.line_taxable_value,
                    "line_tax_amount": line.line_tax_amount,
                    "line_total": line.line_total,
                }
                for line in sorted(lines, key=lambda item: item.line_no)
            ],
        }
    )


# ---------------------------------------------------------------------------
# Adapters for evidence that does not exist yet
#
# 🔴 Phase 3 brings `crm.project`, and Phase 5 the satellite cross-check. Until
# then these return `not_available` rather than a pass. A protocol rather than
# a stub function because the shape is the contract the later phase implements.
# ---------------------------------------------------------------------------


class OperationAreaSource:
    """
    Completed field-operation area for a project, in hectares.

    Implemented in Phase 3 against `crm.project` / `crm.field_visit`. The
    method returning `None` is the honest answer today.
    """

    async def area_ha(
        self, session: AsyncSession, *, invoice: Invoice
    ) -> tuple[Decimal | None, str]:
        return None, (
            "Operation logs are not available: `crm.project` and the field-visit "
            "records land in Phase 3. Billed area cannot be reconciled against "
            "completed work yet."
        )


class GeospatialAreaSource:
    """
    Satellite-measured area for a project's plots, in hectares.

    Phase 5, and the differentiator CLAUDE.md describes — a farmer declares
    3.5 ha, imagery shows 2.1 ha, an agent walks the boundary. Nothing here
    guesses at it in the meantime.
    """

    async def area_ha(
        self, session: AsyncSession, *, invoice: Invoice
    ) -> tuple[Decimal | None, str]:
        return None, (
            "Geospatial area is not available: the satellite cross-check is "
            "Phase 5. Billed area cannot be compared with measured area yet."
        )


class ServicePeriodSource:
    """
    Plots and service periods already billed, for double-billing detection.

    Needs `crm.project_site` populated, which is Phase 3. Note what this does
    *not* do in the meantime: the duplicate-invoice check below still runs,
    because it needs only invoices, and it catches the commonest case.
    """

    async def overlaps(
        self, session: AsyncSession, *, invoice: Invoice
    ) -> tuple[list[dict[str, Any]] | None, str]:
        return None, (
            "Plot and service-period records land in Phase 3 with "
            "`crm.project_site`. Overlapping-service detection cannot run yet; "
            "duplicate-document detection below is unaffected."
        )


@dataclass
class CheckContext:
    """Injected sources, so tests can supply real ones without a Phase 3."""

    operations: OperationAreaSource = field(default_factory=OperationAreaSource)
    geospatial: GeospatialAreaSource = field(default_factory=GeospatialAreaSource)
    service_periods: ServicePeriodSource = field(default_factory=ServicePeriodSource)
    #: Overridable so tests are not time-dependent.
    today: date | None = None

    @property
    def now_date(self) -> date:
        return self.today or datetime.now(UTC).date()


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _financial_year_of(when: date) -> str:
    year = when.year if when.month >= 4 else when.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


def check_lines_present(invoice: Invoice, lines: list[InvoiceLine]) -> list[CheckResult]:
    if lines:
        return []
    return [
        CheckResult(
            code="no_lines",
            severity="error",
            title="The invoice has no lines",
            explanation="A document with no lines has nothing to bill for.",
            blocks_issue=True,
        )
    ]


def check_gstin_format(invoice: Invoice) -> list[CheckResult]:
    """
    🔴 D1 and D2 from INVOICE.md §3, made impossible.

    29 of 105 historical lines carry a GSTIN one character short. That blocks
    the customer's input tax credit and surfaces on *their* GSTR-2B, which is
    the worst place for your mistake to appear.
    """
    raw = (invoice.buyer_gstin or "").strip()

    if not raw:
        if invoice.tax_treatment in TAXABLE_TREATMENTS:
            return [
                CheckResult(
                    code="buyer_gstin_missing",
                    severity="error",
                    title="No buyer GSTIN on a taxable supply",
                    explanation=(
                        "This invoice charges GST but names no buyer GSTIN, so the "
                        "customer cannot claim input tax credit against it. Add the "
                        "GSTIN to the customer record in the registry rather than "
                        "typing it onto the invoice."
                    ),
                    blocks_issue=True,
                )
            ]
        return [
            CheckResult(
                code="buyer_gstin_absent",
                severity="info",
                title="No buyer GSTIN",
                explanation=(
                    "The buyer has no GSTIN recorded. That is expected for an "
                    "unregistered buyer; confirm it is not simply missing."
                ),
            )
        ]

    try:
        normalised = gstin_lib.validate(raw, allow_govt_uin=invoice.buyer_is_govt_uin)
    except gstin_lib.GSTINError as error:
        return [
            CheckResult(
                code="buyer_gstin_invalid",
                severity="error",
                title="The buyer GSTIN is malformed",
                explanation=str(error),
                blocks_issue=True,
                evidence={"gstin": raw},
            )
        ]

    results: list[CheckResult] = []
    if normalised != raw:
        results.append(
            CheckResult(
                code="buyer_gstin_normalised",
                severity="info",
                title="GSTIN normalised",
                explanation=f"'{raw}' was read as '{normalised}'.",
                evidence={"as_entered": raw, "normalised": normalised},
            )
        )
    return results


def check_state_and_treatment(invoice: Invoice) -> list[CheckResult]:
    """
    Place of supply against the buyer's state (INVOICE.md §5.5).

    Both entities are Delhi (07), so a buyer elsewhere is inter-state and takes
    IGST. 🔴 A *suggestion*, and a warning rather than a block — §5.4 is
    unresolved and some TFD billing may be grant disbursement rather than a
    taxable supply. Blocking here would force a guess on the exact question
    the CA has not answered.
    """
    results: list[CheckResult] = []
    buyer_state = invoice.buyer_state_code or gstin_lib.state_code(invoice.buyer_gstin or "")

    if buyer_state and invoice.buyer_gstin:
        gstin_state = gstin_lib.state_code(invoice.buyer_gstin)
        if gstin_state and gstin_state != buyer_state:
            results.append(
                CheckResult(
                    code="buyer_state_conflict",
                    severity="error",
                    title="Buyer state does not match the GSTIN",
                    explanation=(
                        f"The buyer state is recorded as {buyer_state} "
                        f"({gstin_lib.state_name(buyer_state) or 'unknown'}) but the "
                        f"GSTIN begins {gstin_state} "
                        f"({gstin_lib.state_name(gstin_state) or 'unknown'}). One of "
                        f"the two is wrong, and the choice changes the tax treatment."
                    ),
                    blocks_issue=True,
                    evidence={"buyer_state": buyer_state, "gstin_state": gstin_state},
                )
            )
            return results

    supplier_state = "07"
    if invoice.billing_entity is not None and invoice.billing_entity.state_code:
        supplier_state = invoice.billing_entity.state_code

    if buyer_state and invoice.tax_treatment in TAXABLE_TREATMENTS:
        suggested = gstin_lib.derive_tax_treatment(supplier_state, buyer_state)
        if suggested != invoice.tax_treatment:
            results.append(
                CheckResult(
                    code="tax_treatment_unexpected",
                    severity="warning",
                    title=f"Expected {suggested.upper()}, this invoice uses "
                    f"{invoice.tax_treatment.upper()}",
                    explanation=(
                        f"Supplier state {supplier_state}, buyer state {buyer_state}. "
                        f"For a service to a registered person the place of supply is "
                        f"the recipient's location, which suggests "
                        f"{suggested.replace('_', '+').upper()}. Override deliberately "
                        f"if the contract says otherwise — the override is recorded."
                    ),
                    evidence={
                        "supplier_state": supplier_state,
                        "buyer_state": buyer_state,
                        "suggested": suggested,
                        "selected": invoice.tax_treatment,
                    },
                )
            )

    if (
        invoice.place_of_supply_state_code
        and buyer_state
        and invoice.place_of_supply_state_code != buyer_state
    ):
        results.append(
            CheckResult(
                code="place_of_supply_differs",
                severity="info",
                title="Place of supply differs from the buyer's state",
                explanation=(
                    f"Place of supply is {invoice.place_of_supply_state_code}; the "
                    f"buyer is registered in {buyer_state}. Legitimate for work "
                    f"performed elsewhere — confirm it is intentional."
                ),
                evidence={
                    "place_of_supply": invoice.place_of_supply_state_code,
                    "buyer_state": buyer_state,
                },
            )
        )

    return results


def check_arithmetic(invoice: Invoice, lines: list[InvoiceLine]) -> list[CheckResult]:
    """
    🔴 Recompute every line and the header, and compare with what is stored.

    The stored values were computed server-side, so this should never fire —
    which is exactly why it is worth running. If it ever does, something wrote
    money that did not come from `money.py`, and that is the failure worth
    catching before the document leaves the building.
    """
    results: list[CheckResult] = []
    taxable_supply = invoice.tax_treatment in TAXABLE_TREATMENTS
    computed = []

    for line in lines:
        amounts = compute_line(
            qty=line.quantity,
            rate=line.rate,
            tax_rate_pct=invoice.tax_rate_pct,
            rate_is_tax_inclusive=line.rate_is_tax_inclusive,
            taxable_supply=taxable_supply,
        )
        computed.append(amounts)
        if (
            amounts.taxable != line.line_taxable_value
            or amounts.tax != line.line_tax_amount
            or amounts.total != line.line_total
        ):
            results.append(
                CheckResult(
                    code="line_arithmetic_mismatch",
                    severity="error",
                    title=f"Line {line.line_no} does not recompute",
                    explanation=(
                        f"{line.quantity} {line.unit} at {line.rate} gives "
                        f"{amounts.taxable} + {amounts.tax} = {amounts.total}; the "
                        f"line stores {line.line_taxable_value} + "
                        f"{line.line_tax_amount} = {line.line_total}."
                    ),
                    blocks_issue=True,
                    evidence={
                        "line_no": line.line_no,
                        "computed": {
                            "taxable": str(amounts.taxable),
                            "tax": str(amounts.tax),
                            "total": str(amounts.total),
                        },
                        "stored": {
                            "taxable": str(line.line_taxable_value),
                            "tax": str(line.line_tax_amount),
                            "total": str(line.line_total),
                        },
                    },
                )
            )

    if computed:
        header = sum_lines(computed)
        if (
            header.taxable != invoice.taxable_value
            or header.tax != invoice.tax_amount
            or header.total != invoice.total_value
        ):
            results.append(
                CheckResult(
                    code="header_total_mismatch",
                    severity="error",
                    title="The header totals do not match the lines",
                    explanation=(
                        f"The lines sum to {header.taxable} + {header.tax} = "
                        f"{header.total}; the invoice header carries "
                        f"{invoice.taxable_value} + {invoice.tax_amount} = "
                        f"{invoice.total_value}. Rounding is per line, then summed "
                        f"(INVOICE.md §5.1)."
                    ),
                    blocks_issue=True,
                    evidence={
                        "lines_total": str(header.total),
                        "header_total": str(invoice.total_value),
                    },
                )
            )

    return results


def check_inclusive_tax_consistency(
    invoice: Invoice, lines: list[InvoiceLine]
) -> list[CheckResult]:
    """
    🔴 §2.2. Spraying is quoted ex-tax; the Mizoram survey rate already
    contains GST. Mixing the two conventions in one document produces a total
    that is right for neither, and the register overstates revenue by the tax
    fraction on every line that got it wrong.
    """
    if not lines:
        return []

    flags = {line.rate_is_tax_inclusive for line in lines}
    if len(flags) > 1:
        inclusive = [line.line_no for line in lines if line.rate_is_tax_inclusive]
        exclusive = [line.line_no for line in lines if not line.rate_is_tax_inclusive]
        return [
            CheckResult(
                code="mixed_tax_inclusive",
                severity="warning",
                title="Some lines are tax-inclusive and some are not",
                explanation=(
                    f"Lines {inclusive} quote a rate that already contains GST; "
                    f"lines {exclusive} quote it ex-tax. That is legitimate but "
                    f"unusual on one document — confirm both are as contracted, "
                    f"because getting one wrong misstates revenue by the tax fraction."
                ),
                evidence={"inclusive_lines": inclusive, "exclusive_lines": exclusive},
            )
        ]

    if flags == {True} and invoice.tax_treatment not in TAXABLE_TREATMENTS:
        return [
            CheckResult(
                code="inclusive_without_tax",
                severity="warning",
                title="Tax-inclusive rate on a non-taxable treatment",
                explanation=(
                    f"Every line is marked tax-inclusive, but the treatment is "
                    f"'{invoice.tax_treatment}', which separates no tax out. The "
                    f"inclusive flag then has no effect and is probably left over "
                    f"from a copied draft."
                ),
            )
        ]

    return []


def check_financial_year(invoice: Invoice, context: CheckContext) -> list[CheckResult]:
    """
    Invoice date against the financial year and against today.

    A back-dated invoice into a closed FY is a filing problem, and a
    forward-dated one is usually a typed year.
    """
    results: list[CheckResult] = []
    today = context.now_date
    invoice_fy = _financial_year_of(invoice.invoice_date)
    current_fy = _financial_year_of(today)

    if invoice.invoice_date > today:
        results.append(
            CheckResult(
                code="invoice_date_future",
                severity="error",
                title="The invoice date is in the future",
                explanation=(
                    f"Dated {invoice.invoice_date.isoformat()}, today is "
                    f"{today.isoformat()}. A tax invoice cannot be issued ahead "
                    f"of its own date."
                ),
                blocks_issue=True,
                evidence={"invoice_date": invoice.invoice_date.isoformat()},
            )
        )
    elif invoice_fy != current_fy:
        results.append(
            CheckResult(
                code="invoice_date_prior_fy",
                severity="warning",
                title=f"Dated in FY {invoice_fy}, not the current FY {current_fy}",
                explanation=(
                    f"The number will be allocated from the {invoice_fy} series. "
                    f"If that year's returns are filed, raising a document into it "
                    f"is a conversation with your CA before it is a click here."
                ),
                evidence={"invoice_fy": invoice_fy, "current_fy": current_fy},
            )
        )

    if invoice.due_date and invoice.due_date < invoice.invoice_date:
        results.append(
            CheckResult(
                code="due_before_invoice_date",
                severity="error",
                title="The due date precedes the invoice date",
                explanation=(
                    f"Due {invoice.due_date.isoformat()}, dated {invoice.invoice_date.isoformat()}."
                ),
                blocks_issue=True,
            )
        )

    return results


def check_template_fields(invoice: Invoice) -> list[CheckResult]:
    """
    Fields the chosen template prints and this invoice has not filled.

    T3 is the Mizoram survey document: it carries a consignee block, a work
    order reference and a data link, and a blank one prints as an empty box on
    a document a government department will read.
    """
    if invoice.template_code != "T3":
        return []

    missing = [
        name
        for name, value in (
            ("consignee name", invoice.consignee_name),
            ("work order reference", invoice.work_order_ref),
            ("letter reference", invoice.letter_ref),
        )
        if not (value or "").strip()
    ]
    if not missing:
        return []

    return [
        CheckResult(
            code="template_fields_missing",
            severity="warning",
            title=f"Template T3 prints {len(missing)} field(s) this invoice leaves blank",
            explanation=(
                f"Missing: {', '.join(missing)}. T3 renders a consignee block and "
                f"reference lines; blank ones print as empty boxes."
            ),
            evidence={"missing": missing},
        )
    ]


async def check_duplicates(
    session: AsyncSession, invoice: Invoice, lines: list[InvoiceLine]
) -> list[CheckResult]:
    """
    🔴 D3. `TEPL/2026-27/03` and `/04` were cancelled and reissued under the
    same numbers. The unique index makes that impossible now; this catches the
    other shape — the same work billed twice under two different numbers.

    Matched on buyer, total and a date window rather than on an exact tuple: a
    genuine re-bill a fortnight later at the same amount is exactly what a
    human should look at.
    """
    if invoice.total_value <= 0:
        return []

    window_start = invoice.invoice_date - timedelta(days=45)
    window_end = invoice.invoice_date + timedelta(days=45)

    conditions = [
        Invoice.id != invoice.id,
        Invoice.is_deleted.is_(False),
        Invoice.status.notin_(("draft", "discarded", "cancelled")),
        Invoice.total_value == invoice.total_value,
        Invoice.invoice_date.between(window_start, window_end),
    ]
    if invoice.organisation_id is not None:
        conditions.append(Invoice.organisation_id == invoice.organisation_id)
    else:
        conditions.append(func.lower(Invoice.buyer_name) == (invoice.buyer_name or "").lower())

    candidates = list(await session.scalars(select(Invoice).where(and_(*conditions)).limit(5)))
    if not candidates:
        return []

    return [
        CheckResult(
            code="likely_duplicate",
            severity="warning",
            title=f"{len(candidates)} invoice(s) match this buyer, amount and date window",
            explanation=(
                "An invoice for the same customer and the same total already exists "
                "within 45 days: "
                + ", ".join(
                    f"{c.invoice_no or 'draft'} dated {c.invoice_date.isoformat()}"
                    for c in candidates
                )
                + ". Confirm this is a second piece of work rather than the same one "
                "billed twice."
            ),
            evidence={
                "candidates": [
                    {
                        "id": str(c.id),
                        "invoice_no": c.invoice_no,
                        "invoice_date": c.invoice_date.isoformat(),
                        "total_value": str(c.total_value),
                        "status": c.status,
                    }
                    for c in candidates
                ]
            },
        )
    ]


async def check_contract_rate(
    session: AsyncSession, invoice: Invoice, lines: list[InvoiceLine]
) -> list[CheckResult]:
    """
    Every line's rate against the contract or PO rate on file.

    Returns `not_available` when there is no contract row for this customer —
    which is honest, and different from "the rate is fine".
    """
    if not lines:
        return []

    conditions = [
        ContractRate.billing_entity_id == invoice.billing_entity_id,
        ContractRate.valid_from <= invoice.invoice_date,
        or_(ContractRate.valid_to.is_(None), ContractRate.valid_to >= invoice.invoice_date),
    ]
    if invoice.organisation_id is not None:
        conditions.append(ContractRate.organisation_id == invoice.organisation_id)
    else:
        return [
            CheckResult(
                code="contract_rate_not_available",
                severity="info",
                title="No customer linked, so no contract rate to compare",
                explanation=(
                    "This invoice names a buyer by text rather than linking a "
                    "registry organisation, so there is nothing to look a contract "
                    "rate up against. Linking the customer also fixes the GSTIN "
                    "being retyped per invoice (INVOICE.md §3, D2)."
                ),
                not_available=True,
            )
        ]

    contracts = list(await session.scalars(select(ContractRate).where(and_(*conditions))))
    if not contracts:
        return [
            CheckResult(
                code="contract_rate_not_available",
                severity="info",
                title="No contract or PO rate on file for this customer",
                explanation=(
                    "Rate variance cannot be checked because no `crm.contract_rate` "
                    "row covers this customer on this date. Add the agreed rate to "
                    "make the check meaningful."
                ),
                not_available=True,
            )
        ]

    results: list[CheckResult] = []
    for line in lines:
        matched = [
            contract
            for contract in contracts
            if contract.unit == line.unit
            and (
                not contract.buyer_order_no
                or contract.buyer_order_no == (invoice.buyer_order_no or "")
            )
            and (not contract.hsn_sac or contract.hsn_sac == (line.hsn_sac or ""))
        ]
        if not matched:
            continue

        contract = matched[0]
        tolerance = contract.tolerance_pct or Decimal(0)
        if contract.rate == 0:
            continue
        variance = (line.rate - contract.rate) / contract.rate * 100

        if abs(variance) > tolerance:
            results.append(
                CheckResult(
                    code="rate_variance",
                    severity="warning",
                    title=(
                        f"Line {line.line_no} bills {line.rate} against a contracted "
                        f"{contract.rate}"
                    ),
                    explanation=(
                        f"{variance:+.1f}% against the rate agreed in "
                        f"{contract.source_reference or 'the contract on file'}, "
                        f"effective {contract.valid_from.isoformat()}. Tolerance is "
                        f"{tolerance}%."
                    ),
                    evidence={
                        "line_no": line.line_no,
                        "billed_rate": str(line.rate),
                        "contract_rate": str(contract.rate),
                        "variance_pct": f"{variance:.2f}",
                        "contract_id": str(contract.id),
                        "source_reference": contract.source_reference,
                    },
                )
            )
        if contract.rate_is_tax_inclusive != line.rate_is_tax_inclusive:
            results.append(
                CheckResult(
                    code="rate_inclusive_conflict",
                    severity="warning",
                    title=f"Line {line.line_no} disagrees with the contract on tax inclusion",
                    explanation=(
                        f"The contract records the rate as "
                        f"{'inclusive' if contract.rate_is_tax_inclusive else 'exclusive'} "
                        f"of GST; this line treats it as "
                        f"{'inclusive' if line.rate_is_tax_inclusive else 'exclusive'}. "
                        f"The difference is the tax fraction of the line."
                    ),
                    evidence={"line_no": line.line_no, "contract_id": str(contract.id)},
                )
            )

    return results


async def check_billed_area(
    session: AsyncSession, invoice: Invoice, lines: list[InvoiceLine], context: CheckContext
) -> list[CheckResult]:
    """
    Billed area against completed operations and against measured area.

    🔴 Both sources return `not_available` until Phases 3 and 5. The check
    still runs and still reports, because a silent skip reads as a pass on the
    exact question — "did we bill for more acres than we sprayed" — that this
    system exists to be able to answer.
    """
    billed_ha = sum(
        (line.quantity_ha for line in lines if line.quantity_ha is not None), Decimal(0)
    )
    if billed_ha <= 0:
        return []

    results: list[CheckResult] = []

    for label, source, code in (
        ("completed operations", context.operations, "billed_area_vs_operations"),
        ("measured (satellite) area", context.geospatial, "billed_area_vs_geospatial"),
    ):
        area, reason = await source.area_ha(session, invoice=invoice)
        if area is None:
            results.append(
                CheckResult(
                    code=f"{code}_not_available",
                    severity="info",
                    title=f"Billed area not reconciled against {label}",
                    explanation=reason,
                    evidence={"billed_ha": str(billed_ha)},
                    not_available=True,
                )
            )
            continue

        if area <= 0:
            continue
        variance = (billed_ha - area) / area * 100
        if abs(variance) > 10:
            results.append(
                CheckResult(
                    code=code,
                    severity="warning",
                    title=f"Billed area is {variance:+.1f}% against {label}",
                    explanation=(
                        f"This invoice bills {billed_ha} ha; {label} gives {area} ha. "
                        f"Landholding and worked area are commonly over-reported by "
                        f"20–40%, so a variance this size is worth a look before the "
                        f"document goes out."
                    ),
                    evidence={
                        "billed_ha": str(billed_ha),
                        "source_ha": str(area),
                        "variance_pct": f"{variance:.2f}",
                    },
                )
            )

    overlaps, reason = await context.service_periods.overlaps(session, invoice=invoice)
    if overlaps is None:
        results.append(
            CheckResult(
                code="service_overlap_not_available",
                severity="info",
                title="Overlapping service periods not checked",
                explanation=reason,
                not_available=True,
            )
        )
    elif overlaps:
        results.append(
            CheckResult(
                code="service_overlap",
                severity="warning",
                title=f"{len(overlaps)} plot/period overlap(s) with earlier billing",
                explanation=(
                    "Work already billed covers some of the same plots and dates. "
                    "Confirm this is a second operation rather than the same one."
                ),
                evidence={"overlaps": overlaps},
            )
        )

    return results


async def check_organisation_match(session: AsyncSession, invoice: Invoice) -> list[CheckResult]:
    """The buyer snapshot against the linked registry organisation."""
    if invoice.organisation_id is None:
        return [
            CheckResult(
                code="no_organisation_link",
                severity="warning",
                title="This invoice is not linked to a registry customer",
                explanation=(
                    "The buyer is typed onto the document rather than linked to "
                    "`core.organisation`. That is how one customer's GSTIN ended up "
                    "recorded two ways in the historical data (INVOICE.md §3, D2), "
                    "and it means revenue cannot be joined to the CRM."
                ),
            )
        ]

    org = await session.scalar(
        select(Organisation).where(Organisation.id == invoice.organisation_id)
    )
    if org is None:
        return [
            CheckResult(
                code="organisation_missing",
                severity="error",
                title="The linked customer no longer exists",
                explanation="The organisation this invoice points at has been removed.",
                blocks_issue=True,
            )
        ]

    results: list[CheckResult] = []
    org_gstin = (getattr(org, "gstin", None) or "").strip().upper()
    invoice_gstin = (invoice.buyer_gstin or "").strip().upper()

    if org_gstin and invoice_gstin and org_gstin != invoice_gstin:
        results.append(
            CheckResult(
                code="organisation_gstin_mismatch",
                severity="error",
                title="The invoice GSTIN differs from the customer record",
                explanation=(
                    f"The registry holds {org_gstin} for {org.name}; this invoice "
                    f"carries {invoice_gstin}. A customer with a GSTIN per state (as "
                    f"Syngenta has) needs the right state's registration, and the "
                    f"registry is where that is corrected."
                ),
                blocks_issue=True,
                evidence={"organisation_gstin": org_gstin, "invoice_gstin": invoice_gstin},
            )
        )
    elif not org_gstin and invoice_gstin:
        results.append(
            CheckResult(
                code="organisation_gstin_absent",
                severity="info",
                title="The customer record has no GSTIN",
                explanation=(
                    f"{org.name} holds no GSTIN in the registry while this invoice "
                    f"carries one. Adding it to the customer stops it being retyped."
                ),
                evidence={"invoice_gstin": invoice_gstin},
            )
        )

    return results


async def check_gstin_verification(
    session: AsyncSession, invoice: Invoice, context: CheckContext
) -> list[CheckResult]:
    """
    The live GSTIN verification behind this invoice, and how fresh it is.

    🔴 INVOICE.md §12.4: an unavailable check is never a valid one, and a
    customer whose `gstin_policy` is `require_current` blocks on a stale or
    missing verification rather than warning.
    """
    from backend.config import settings

    raw = (invoice.buyer_gstin or "").strip().upper()
    if not raw or invoice.buyer_is_govt_uin:
        return []

    policy = "warn"
    if invoice.organisation_id is not None:
        org = await session.scalar(
            select(Organisation).where(Organisation.id == invoice.organisation_id)
        )
        if org is not None:
            policy = getattr(org, "gstin_policy", "warn") or "warn"

    verification = await session.scalar(
        select(GstinVerification)
        .where(
            GstinVerification.billing_entity_id == invoice.billing_entity_id,
            GstinVerification.gstin == raw,
        )
        .order_by(GstinVerification.checked_at.desc())
        .limit(1)
    )

    requires_current = policy == "require_current"

    if verification is None:
        return [
            CheckResult(
                code="gstin_not_verified",
                severity="error" if requires_current else "warning",
                title="This GSTIN has never been verified against the registry",
                explanation=(
                    "A checksum-valid GSTIN is not necessarily an active one. Run "
                    "Verify to check status, legal name and state with the provider."
                    + (
                        " This customer's policy requires a current verification before issue."
                        if requires_current
                        else ""
                    )
                ),
                blocks_issue=requires_current,
                evidence={"gstin": raw, "policy": policy},
            )
        ]

    results: list[CheckResult] = []
    age_days = (datetime.now(UTC) - verification.checked_at).days

    if verification.status == "verification_unavailable":
        results.append(
            CheckResult(
                code="gstin_verification_unavailable",
                severity="error" if requires_current else "warning",
                title="The last verification could not reach the provider",
                explanation=(
                    "The provider was unavailable, so this GSTIN's status is "
                    "unknown — which is not the same as valid. Retry before issue, "
                    "or override with a reason if the work cannot wait."
                ),
                blocks_issue=requires_current,
                evidence={"checked_at": verification.checked_at.isoformat()},
            )
        )
    elif verification.status not in GSTIN_OK_STATUSES:
        results.append(
            CheckResult(
                code="gstin_not_active",
                severity="error",
                title=f"The registry reports this GSTIN as {verification.status}",
                explanation=(
                    f"{raw} is '{verification.status}' with the provider"
                    + (
                        f", cancelled {verification.cancellation_date.isoformat()}"
                        if verification.cancellation_date
                        else ""
                    )
                    + ". Billing GST to a cancelled registration denies the customer "
                    "input credit and raises a mismatch on their return."
                ),
                blocks_issue=True,
                evidence={
                    "status": verification.status,
                    "checked_at": verification.checked_at.isoformat(),
                },
            )
        )
    elif age_days > settings.gstin_stale_after_days:
        results.append(
            CheckResult(
                code="gstin_verification_stale",
                severity="error" if requires_current else "info",
                title=f"The verification is {age_days} days old",
                explanation=(
                    f"Last checked {verification.checked_at.date().isoformat()}. "
                    f"Registrations are cancelled without notice; Verify again if "
                    f"this customer matters."
                ),
                blocks_issue=requires_current,
                evidence={"age_days": age_days, "policy": policy},
            )
        )

    if verification.legal_name and invoice.buyer_name:
        registry_name = verification.legal_name.strip().lower()
        invoice_name = invoice.buyer_name.strip().lower()
        if (
            registry_name
            and registry_name not in invoice_name
            and invoice_name not in registry_name
        ):
            results.append(
                CheckResult(
                    code="gstin_name_mismatch",
                    severity="warning",
                    title="The buyer name does not resemble the registered legal name",
                    explanation=(
                        f"The registry returns '{verification.legal_name}'; this "
                        f"invoice bills '{invoice.buyer_name}'. A trade name is fine; "
                        f"a different company is not."
                    ),
                    evidence={
                        "registry_legal_name": verification.legal_name,
                        "invoice_buyer_name": invoice.buyer_name,
                    },
                )
            )

    if (
        verification.state_code
        and invoice.buyer_state_code
        and verification.state_code != invoice.buyer_state_code
    ):
        results.append(
            CheckResult(
                code="gstin_state_mismatch",
                severity="error",
                title="The registered state differs from the buyer state",
                explanation=(
                    f"The registry places this GSTIN in "
                    f"{verification.state_code}; the invoice records the buyer "
                    f"in {invoice.buyer_state_code}. This changes whether the "
                    f"supply is inter-state."
                ),
                blocks_issue=True,
                evidence={
                    "registry_state": verification.state_code,
                    "invoice_state": invoice.buyer_state_code,
                },
            )
        )

    return results


def check_historical(invoice: Invoice) -> list[CheckResult]:
    if not invoice.is_historical:
        return []
    return [
        CheckResult(
            code="historical_record",
            severity="warning",
            title="This is an imported historical record",
            explanation=(
                "Imported invoices reproduce an original document rather than "
                "rendering today's template. Issuing one through this system would "
                "allocate a new number to a document that already has one."
            ),
            blocks_issue=True,
        )
    ]


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


async def run_checks(
    session: AsyncSession,
    invoice: Invoice,
    *,
    context: CheckContext | None = None,
) -> CheckReport:
    """
    Run every check against one invoice.

    Ordered by what a person needs to see first: things that stop the document,
    then things that should be looked at, then things worth knowing. The list
    is returned sorted by severity so a UI does not have to decide.
    """
    context = context or CheckContext()
    lines = list(invoice.lines)

    results: list[CheckResult] = []
    results += check_lines_present(invoice, lines)
    results += check_historical(invoice)
    results += check_gstin_format(invoice)
    results += check_state_and_treatment(invoice)
    results += check_arithmetic(invoice, lines)
    results += check_inclusive_tax_consistency(invoice, lines)
    results += check_financial_year(invoice, context)
    results += check_template_fields(invoice)
    results += await check_organisation_match(session, invoice)
    results += await check_gstin_verification(session, invoice, context)
    results += await check_duplicates(session, invoice, lines)
    results += await check_contract_rate(session, invoice, lines)
    results += await check_billed_area(session, invoice, lines, context)

    order = {"error": 0, "warning": 1, "info": 2}
    results.sort(key=lambda item: (order[item.severity], not item.blocks_issue, item.code))

    acknowledged = set(
        await session.scalars(
            select(InvoiceCheckAck.check_code).where(InvoiceCheckAck.invoice_id == invoice.id)
        )
    )

    return CheckReport(
        invoice_id=invoice.id,
        invoice_sha256=fingerprint(invoice, lines),
        results=results,
        acknowledged_codes=frozenset(acknowledged),
    )


async def gstin_check_evidence(
    session: AsyncSession, invoice: Invoice, report: CheckReport, *, actor: uuid.UUID
) -> InvoiceGstinCheck:
    """
    Freeze the GSTIN findings onto the invoice at issue.

    🔴 Written once, immutable by trigger. Re-verifying next year creates a new
    verification row; what this invoice was checked against does not change,
    because a record of a decision that moves afterwards is not a record.
    """
    raw = (invoice.buyer_gstin or "").strip().upper()
    gstin_results = [r for r in report.results if r.code.startswith("gstin_") or "gstin" in r.code]

    local_result = "valid"
    if invoice.buyer_is_govt_uin:
        local_result = "govt_uin"
    elif any(r.code == "buyer_gstin_invalid" for r in report.results):
        local_result = "invalid_format"
    elif not raw:
        local_result = "absent"

    verification = None
    if raw:
        verification = await session.scalar(
            select(GstinVerification)
            .where(
                GstinVerification.billing_entity_id == invoice.billing_entity_id,
                GstinVerification.gstin == raw,
            )
            .order_by(GstinVerification.checked_at.desc())
            .limit(1)
        )

    row = InvoiceGstinCheck(
        invoice_id=invoice.id,
        verification_id=verification.id if verification else None,
        checked_gstin=raw or "(none)",
        local_result=local_result,
        live_status=verification.status if verification else None,
        blocking_reasons=[r.code for r in gstin_results if r.blocks_issue],
        mismatches=[r.as_dict() for r in gstin_results if not r.blocks_issue],
        created_at=datetime.now(UTC),
        created_by=actor,
    )
    session.add(row)
    await session.flush()
    return row
