"""
The AI proposal service — propose, confirm, apply.

🔴 **The whole trust boundary lives here**, and it is three rules:

1. **A proposal is a record before it is an effect.** Nothing a model decides
   reaches an invoice without a `crm.ai_proposal` row that names the actor, the
   evidence, the before-state and the exact patch.

2. **Confirmation binds to a hash.** A human confirms *these bytes*. If the
   draft moved underneath — someone edited it, a payment landed — the hash no
   longer matches and the confirmation is refused rather than applied to state
   nobody reviewed.

3. **The applier is deterministic and narrow.** It writes a small set of fields
   onto an unnumbered draft, and computes every amount through `money.py`.
   `validate_patch` rejects an unknown field rather than ignoring it, because
   an ignored field is an instruction the human read in the diff and the system
   silently dropped.

INVOICE.md §12.2, §12.5 and phase I-7.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import gstin as gstin_lib
from backend.domain import retrieval
from backend.domain.hashing import matches, sha256_of
from backend.domain.scoping import EntityScope
from backend.models.billing import BillingEntity, Invoice, InvoiceLine
from backend.models.business import Organisation
from backend.models.copilot import MUTATING_ACTIONS, AiProposal
from backend.money import compute_line, rupees_in_words, sum_lines, to_hectares
from backend.providers.copilot import Draft, UnsafeRequest, get_provider

#: 🔴 The only fields a proposal may set on a draft. An allow-list, not a
#: deny-list: a deny-list has to anticipate every dangerous field, and this has
#: to anticipate every safe one. Note what is absent — `invoice_no`, `status`,
#: `taxable_value`, `tax_amount`, `total_value`, `issued_at`, `pdf_sha256`,
#: `created_by`, `billing_entity_id`. None of those are the copilot's to touch.
PATCHABLE_HEADER_FIELDS = frozenset(
    {
        "invoice_date",
        "due_date",
        "buyer_name",
        "buyer_address",
        "buyer_gstin",
        "buyer_state_code",
        "buyer_is_govt_uin",
        "buyer_order_no",
        "work_order_ref",
        "letter_ref",
        "payment_terms",
        "organisation_id",
        "place_of_supply_state_code",
        "consignee_name",
        "consignee_address",
        "consignee_gstin",
        "data_link_url",
        "notes",
        "tax_treatment",
        "tax_rate_pct",
    }
)

#: Fields a proposed line may carry. 🔴 No amounts: `line_taxable_value`,
#: `line_tax_amount` and `line_total` are computed, and a patch that names one
#: is rejected outright rather than having it stripped.
PATCHABLE_LINE_FIELDS = frozenset(
    {
        "line_no",
        "description",
        "hsn_sac",
        "quantity",
        "unit",
        "rate",
        "rate_is_tax_inclusive",
        "location_note",
    }
)

FORBIDDEN_FIELDS = frozenset(
    {
        "id",
        "invoice_no",
        "financial_year",
        "status",
        "issued_at",
        "cancelled_at",
        "cancellation_reason",
        "taxable_value",
        "tax_amount",
        "total_value",
        "amount_in_words",
        "line_taxable_value",
        "line_tax_amount",
        "line_total",
        "pdf_sha256",
        "pdf_object_id",
        "billing_entity_id",
        "entity_code",
        "is_deleted",
        "is_historical",
        "created_by",
        "created_at",
    }
)

VALID_UNITS = frozenset({"acre", "sq_km", "hectare", "each", "lump_sum", "day", "hour"})
VALID_TREATMENTS = frozenset({"igst", "cgst_sgst", "zero_rated", "exempt", "grant"})

TAXABLE_TREATMENTS = frozenset({"igst", "cgst_sgst"})


class ProposalError(HTTPException):
    """A 4xx with a message the person reading the diff can act on."""

    def __init__(self, detail: str, code: int = status.HTTP_400_BAD_REQUEST) -> None:
        super().__init__(code, detail)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_patch(patch: dict[str, Any], *, action: str) -> list[str]:
    """
    Check a proposed patch against the allow-list. Returns problems, if any.

    🔴 Unknown fields are an error, not a shrug — the same rule CLAUDE.md
    applies to query filters, for the same reason. A field silently dropped is
    a change the human approved in the diff and the system did not make, and
    nobody finds out until the document is wrong.
    """
    problems: list[str] = []

    if action not in MUTATING_ACTIONS and patch:
        return [
            (
                f"Action '{action}' proposes no changes, but the patch is not "
                f"empty. An explanation cannot carry a mutation."
            )
        ]

    header = {k: v for k, v in patch.items() if k != "lines"}
    for key in header:
        if key in FORBIDDEN_FIELDS:
            problems.append(
                f"'{key}' cannot be proposed. Numbering, status, money and "
                f"issued-document fields are computed or set by a person, never "
                f"patched."
            )
        elif key not in PATCHABLE_HEADER_FIELDS:
            problems.append(f"'{key}' is not a field a proposal may set.")

    treatment = header.get("tax_treatment")
    if treatment is not None and treatment not in VALID_TREATMENTS:
        problems.append(f"'{treatment}' is not a tax treatment.")

    gstin = header.get("buyer_gstin")
    if gstin:
        try:
            gstin_lib.validate(str(gstin), allow_govt_uin=bool(header.get("buyer_is_govt_uin")))
        except gstin_lib.GSTINError as error:
            problems.append(f"Proposed buyer GSTIN: {error}")

    lines = patch.get("lines")
    if lines is not None:
        if not isinstance(lines, list):
            problems.append("'lines' must be a list.")
        else:
            for index, line in enumerate(lines, 1):
                if not isinstance(line, dict):
                    problems.append(f"Line {index} is not an object.")
                    continue
                for key in line:
                    if key in FORBIDDEN_FIELDS:
                        problems.append(
                            f"Line {index}: '{key}' cannot be proposed — line amounts "
                            f"are computed from quantity and rate by the server."
                        )
                    elif key not in PATCHABLE_LINE_FIELDS:
                        problems.append(f"Line {index}: '{key}' is not a line field.")

                unit = line.get("unit")
                if unit is not None and unit not in VALID_UNITS:
                    problems.append(
                        f"Line {index}: '{unit}' is not a billing unit. "
                        f"One of: {', '.join(sorted(VALID_UNITS))}."
                    )
                for numeric in ("quantity", "rate"):
                    value = line.get(numeric)
                    if value is not None:
                        try:
                            if Decimal(str(value)) < 0:
                                problems.append(f"Line {index}: {numeric} cannot be negative.")
                        except (InvalidOperation, TypeError, ValueError):
                            # Narrow on purpose: these are the three ways a
                            # value that is not a number reaches `Decimal`.
                            # A broader catch here would swallow a real bug in
                            # validation, which is the one place that must not
                            # fail quietly.
                            problems.append(f"Line {index}: {numeric} '{value}' is not a number.")

    return problems


async def validate_references(
    session: AsyncSession, scope: EntityScope, patch: dict[str, Any]
) -> list[str]:
    """
    🔴 Every id in a patch must resolve inside the caller's scope.

    This is the check that stops "update the buyer to <an organisation from
    another tenant>" — the id would be perfectly valid and belong to somebody
    else, and without this the applier would happily write it.
    """
    problems: list[str] = []

    organisation_id = patch.get("organisation_id")
    if organisation_id:
        try:
            parsed = uuid.UUID(str(organisation_id))
        except (ValueError, AttributeError):
            return [f"'{organisation_id}' is not an organisation id."]

        org = await session.scalar(
            select(Organisation).where(
                Organisation.id == parsed, Organisation.is_deleted.is_(False)
            )
        )
        if org is None:
            problems.append("The proposed customer does not exist or is not visible to you.")

    return problems


# ---------------------------------------------------------------------------
# Building a patch from a provider's draft
# ---------------------------------------------------------------------------


@dataclass
class BuiltProposal:
    patch: dict[str, Any]
    evidence: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    missing: list[str]
    confidence: Decimal | None


async def _patch_from_draft(
    session: AsyncSession,
    scope: EntityScope,
    draft: Draft,
    *,
    context: dict[str, Any],
    on_date: date,
) -> BuiltProposal:
    """
    Turn a provider's reading into a validated patch with its citations.

    Every populated field either links to a retrieval record or is marked as
    coming from the user's own words — the exit gate for I-7 requires exactly
    that, and it is the difference between a suggestion and an assertion.
    """
    patch: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    missing = list(draft.missing)

    if draft.invoice_date:
        patch["invoice_date"] = draft.invoice_date.isoformat()
        evidence.append(
            {"field": "invoice_date", "kind": "user_provided", "label": "stated in your message"}
        )

    if draft.buyer_order_no:
        patch["buyer_order_no"] = draft.buyer_order_no
        evidence.append(
            {"field": "buyer_order_no", "kind": "user_provided", "label": "stated in your message"}
        )
    if draft.work_order_ref:
        patch["work_order_ref"] = draft.work_order_ref
        evidence.append(
            {"field": "work_order_ref", "kind": "user_provided", "label": "stated in your message"}
        )

    # Resolve the customer against the registry rather than trusting the name.
    organisation: dict[str, Any] | None = None
    if draft.buyer_hint:
        candidates = [
            org
            for org in context.get("organisations", [])
            if draft.buyer_hint.lower() in str(org.get("name", "")).lower()
            or str(org.get("name", "")).lower() in draft.buyer_hint.lower()
        ]
        if len(candidates) == 1:
            organisation = candidates[0]
        elif len(candidates) > 1:
            # 🔴 Syngenta holds a GSTIN per state. Two plausible matches are
            # shown, never silently resolved (INVOICE.md §12.3 A).
            warnings.append(
                {
                    "code": "ambiguous_customer",
                    "severity": "warning",
                    "message": (
                        f"{len(candidates)} customers match '{draft.buyer_hint}': "
                        + ", ".join(
                            f"{c['name']} ({c.get('gstin') or 'no GSTIN'})" for c in candidates[:4]
                        )
                        + ". Pick the right registration — they differ by state."
                    ),
                    "candidates": [
                        {"id": c["id"], "name": c["name"], "gstin": c.get("gstin")}
                        for c in candidates[:6]
                    ],
                }
            )
            missing.append("buyer")

    if organisation is not None:
        patch["organisation_id"] = organisation["id"]
        patch["buyer_name"] = organisation["name"]
        evidence.append(
            {
                "field": "organisation_id",
                "kind": "organisation",
                "id": organisation["id"],
                "label": organisation["name"],
            }
        )
        if organisation.get("gstin"):
            patch["buyer_gstin"] = organisation["gstin"]
            state = gstin_lib.state_code(organisation["gstin"])
            if state:
                patch["buyer_state_code"] = state
            evidence.append(
                {
                    "field": "buyer_gstin",
                    "kind": "organisation",
                    "id": organisation["id"],
                    "label": f"GSTIN on the customer record: {organisation['gstin']}",
                }
            )
        else:
            warnings.append(
                {
                    "code": "customer_has_no_gstin",
                    "severity": "warning",
                    "message": (
                        f"{organisation['name']} has no GSTIN in the registry. Add it "
                        f"to the customer record rather than typing it onto the "
                        f"invoice — that is what stopped one customer's GSTIN being "
                        f"recorded two ways in the historical data."
                    ),
                }
            )
    elif draft.buyer_gstin:
        # A GSTIN stated in the request and no matched customer. Recorded, and
        # flagged, because the registry is where a GSTIN should come from.
        patch["buyer_gstin"] = draft.buyer_gstin
        warnings.append(
            {
                "code": "gstin_not_from_registry",
                "severity": "warning",
                "message": (
                    "The GSTIN came from your message rather than a customer record. "
                    "Link the customer so it is entered once and never retyped."
                ),
            }
        )

    # Lines. 🔴 Only quantity, unit, rate and description; amounts follow.
    proposed_lines: list[dict[str, Any]] = []
    for index, line in enumerate(draft.lines, 1):
        entry: dict[str, Any] = {"line_no": index}
        if line.description:
            entry["description"] = line.description
        if line.quantity is not None:
            entry["quantity"] = str(line.quantity)
        if line.unit:
            entry["unit"] = line.unit
        if line.rate is not None:
            entry["rate"] = str(line.rate)
        if line.rate_is_tax_inclusive is not None:
            entry["rate_is_tax_inclusive"] = line.rate_is_tax_inclusive

        hsn = line.hsn_sac
        if line.description:
            suggestion = await retrieval.suggest_tax_code(
                session, description=line.description, on_date=on_date
            )
            if suggestion:
                hsn = hsn or suggestion["code"]
                evidence.append(
                    {
                        "field": f"lines[{index}].hsn_sac",
                        "kind": "tax_code",
                        "id": suggestion["evidence"]["id"],
                        "label": (
                            f"{suggestion['code']} — {suggestion['description']}, "
                            f"effective {suggestion['effective_from']}"
                        ),
                        "review_status": suggestion["review_status"],
                        "citation": suggestion["citation"],
                    }
                )
                if not suggestion["is_approved"]:
                    warnings.append(
                        {
                            "code": "tax_code_unreviewed",
                            "severity": "warning",
                            "message": (
                                f"The HSN/SAC suggestion {suggestion['code']} has not "
                                f"been reviewed by a CA. It is a starting point, not "
                                f"a verified classification."
                            ),
                        }
                    )
        if hsn:
            entry["hsn_sac"] = hsn

        contract = context.get("contract_rate")
        if contract and line.rate is not None:
            contract_rate = Decimal(str(contract["rate"]))
            if contract_rate and line.rate != contract_rate:
                warnings.append(
                    {
                        "code": "rate_differs_from_contract",
                        "severity": "warning",
                        "message": (
                            f"Line {index} proposes {line.rate} against a contracted "
                            f"{contract_rate} "
                            f"({contract.get('source_reference') or 'contract on file'})."
                        ),
                    }
                )
            evidence.append(
                {
                    "field": f"lines[{index}].rate",
                    "kind": "contract_rate",
                    "id": contract["id"],
                    "label": contract["evidence"]["label"],
                }
            )
        elif line.rate is not None:
            evidence.append(
                {
                    "field": f"lines[{index}].rate",
                    "kind": "user_provided",
                    "label": "stated in your message",
                }
            )

        if line.quantity is not None:
            evidence.append(
                {
                    "field": f"lines[{index}].quantity",
                    "kind": "user_provided",
                    "label": "stated in your message",
                }
            )

        proposed_lines.append(entry)

    if proposed_lines:
        patch["lines"] = proposed_lines

    for note in draft.notes:
        warnings.append({"code": "provider_note", "severity": "info", "message": note})

    # 🔴 Tax treatment is never proposed. §5.4 is open; the person selects it
    # and the checks module offers a suggestion with its reasoning.
    if "tax_treatment" not in patch:
        warnings.append(
            {
                "code": "tax_treatment_not_proposed",
                "severity": "info",
                "message": (
                    "Tax treatment was not set. It stays a person's choice until the "
                    "CA resolves whether TFD billing to mills is a taxable supply or "
                    "a grant (INVOICE.md §5.4). The draft screen suggests one with "
                    "its reasoning."
                ),
            }
        )

    return BuiltProposal(
        patch=patch,
        evidence=evidence,
        warnings=warnings,
        missing=sorted(set(missing)),
        confidence=draft.confidence,
    )


# ---------------------------------------------------------------------------
# Snapshots and hashing
# ---------------------------------------------------------------------------


def snapshot(invoice: Invoice | None) -> dict[str, Any]:
    """
    The before-state a diff is rendered against, and part of the hash.

    None for a create — an empty snapshot and a snapshot of an empty invoice
    are different things, and hashing them the same would let a confirmation
    for a create apply to an update.
    """
    if invoice is None:
        return {"exists": False}

    return {
        "exists": True,
        "id": str(invoice.id),
        "status": invoice.status,
        "invoice_date": invoice.invoice_date.isoformat(),
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "buyer_name": invoice.buyer_name,
        "buyer_gstin": invoice.buyer_gstin,
        "buyer_state_code": invoice.buyer_state_code,
        "buyer_order_no": invoice.buyer_order_no,
        "organisation_id": str(invoice.organisation_id) if invoice.organisation_id else None,
        "tax_treatment": invoice.tax_treatment,
        "tax_rate_pct": str(invoice.tax_rate_pct),
        "taxable_value": str(invoice.taxable_value),
        "tax_amount": str(invoice.tax_amount),
        "total_value": str(invoice.total_value),
        "lines": [
            {
                "line_no": line.line_no,
                "description": line.description,
                "hsn_sac": line.hsn_sac,
                "quantity": str(line.quantity),
                "unit": line.unit,
                "rate": str(line.rate),
                "rate_is_tax_inclusive": line.rate_is_tax_inclusive,
                "line_total": str(line.line_total),
            }
            for line in sorted(invoice.lines, key=lambda item: item.line_no)
        ],
    }


def proposal_hash(
    *, action: str, patch: dict[str, Any], before: dict[str, Any], entity_id: uuid.UUID
) -> bytes:
    """
    🔴 What a confirmation binds to.

    The before-state is *in* the hash. That is the whole mechanism: a draft
    edited between proposal and confirmation produces a different before-state,
    the hash no longer matches, and the confirmation is refused instead of
    applying a diff against state the human never saw.
    """
    return sha256_of({"action": action, "patch": patch, "before": before, "entity": str(entity_id)})


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


async def create_proposal(
    session: AsyncSession,
    scope: EntityScope,
    *,
    request_text: str,
    action: str,
    billing_entity_id: uuid.UUID,
    invoice: Invoice | None = None,
    source: str = "web",
) -> AiProposal:
    """
    Read a request, retrieve evidence, ask a provider, validate, record.

    Nothing is written to an invoice. The row this returns is a proposal in
    `pending`, and the caller renders its diff.
    """
    from backend.config import settings

    scope.check(billing_entity_id, what="billing entity")

    if invoice is not None:
        scope.check(invoice.billing_entity_id, what="invoice")
        # 🔴 The copilot may touch an unnumbered draft and nothing else.
        if invoice.status != "draft" or invoice.invoice_no:
            raise ProposalError(
                f"This invoice is {invoice.status}"
                + (f" and numbered {invoice.invoice_no}" if invoice.invoice_no else "")
                + ". The copilot may only change an unnumbered draft — an issued "
                "document exists in someone else's accounts."
            )

    started = datetime.now(UTC)
    on_date = invoice.invoice_date if invoice is not None else date.today()

    context = await retrieval.build_context(
        session,
        scope,
        request_text=request_text,
        organisation_id=invoice.organisation_id if invoice else None,
        on_date=on_date,
    )
    context["action"] = action

    provider = get_provider()
    try:
        draft = await provider.propose(request_text, context=context)
    except UnsafeRequest as unsafe:
        # 🔴 Recorded as a failed proposal rather than a bare 400. The
        # evaluation summary counts these, and a refusal nobody counts is a
        # refusal nobody can prove kept happening.
        row = AiProposal(
            billing_entity_id=billing_entity_id,
            actor_user_id=scope.user_id,
            action=action if action in MUTATING_ACTIONS else "explain_total",
            status="failed",
            invoice_id=invoice.id if invoice else None,
            model=getattr(provider, "model", None),
            provider=getattr(provider, "name", None),
            prompt_version=settings.copilot_prompt_version,
            proposal_sha256=sha256_of({"refused": unsafe.action}),
            input_sha256=sha256_of({"request": request_text}),
            evidence=[],
            before_snapshot=snapshot(invoice),
            proposed_patch={},
            warnings=[{"code": "unsafe_request", "severity": "error", "message": str(unsafe)}],
            missing_fields=[],
            expires_at=started + timedelta(minutes=settings.copilot_proposal_ttl_minutes),
            created_at=started,
            error=f"refused: {unsafe.action}",
        )
        session.add(row)
        await session.flush()
        raise ProposalError(str(unsafe)) from unsafe

    built = await _patch_from_draft(session, scope, draft, context=context, on_date=on_date)

    problems = validate_patch(built.patch, action=action)
    problems += await validate_references(session, scope, built.patch)

    before = snapshot(invoice)
    digest = proposal_hash(
        action=action, patch=built.patch, before=before, entity_id=billing_entity_id
    )

    warnings = list(built.warnings)
    proposal_status = "pending"
    error = None
    if problems:
        # A patch that fails validation is stored as `failed`, with the reasons
        # visible. Discarding it would lose the evidence that the provider
        # produced something it should not have.
        proposal_status = "failed"
        error = "; ".join(problems)
        warnings.append(
            {"code": "invalid_patch", "severity": "error", "message": error, "problems": problems}
        )

    row = AiProposal(
        billing_entity_id=billing_entity_id,
        actor_user_id=scope.user_id,
        action=action,
        status=proposal_status,
        invoice_id=invoice.id if invoice else None,
        model=getattr(provider, "model", None),
        provider=getattr(provider, "name", None),
        prompt_version=settings.copilot_prompt_version,
        proposal_sha256=digest,
        input_sha256=sha256_of({"request": request_text, "source": source}),
        evidence=built.evidence,
        before_snapshot=before,
        proposed_patch=built.patch,
        warnings=warnings,
        missing_fields=built.missing,
        confidence=built.confidence,
        expires_at=started + timedelta(minutes=settings.copilot_proposal_ttl_minutes),
        created_at=started,
        latency_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
        error=error,
    )
    session.add(row)
    await session.flush()
    return row


async def load_proposal(
    session: AsyncSession, scope: EntityScope, proposal_id: uuid.UUID
) -> AiProposal:
    """Fetch one, scoped. 🔴 404 across a tenant boundary, never 403."""
    row = await session.scalar(select(AiProposal).where(AiProposal.id == proposal_id))
    if row is None:
        raise ProposalError("No such proposal.", status.HTTP_404_NOT_FOUND)
    scope.check(row.billing_entity_id, what="proposal")
    return row


def _expire_if_due(proposal: AiProposal) -> None:
    if proposal.status == "pending" and proposal.expires_at <= datetime.now(UTC):
        proposal.status = "expired"


async def confirm_proposal(
    session: AsyncSession,
    scope: EntityScope,
    proposal: AiProposal,
    *,
    proposal_sha256: str,
) -> AiProposal:
    """
    Record that a named human accepted exactly these bytes.

    🔴 Idempotent. Confirming an already-confirmed proposal with the same hash
    returns it unchanged rather than erroring — a client retrying a request it
    is not sure landed must not be punished for it, and the second call has no
    additional effect.
    """
    _expire_if_due(proposal)

    if proposal.status == "confirmed":
        if not matches(proposal.proposal_sha256, proposal_sha256):
            raise ProposalError(
                "This proposal is already confirmed, and the hash you sent does "
                "not match the one that was confirmed."
            )
        return proposal

    if proposal.status != "pending":
        raise ProposalError(
            f"A {proposal.status} proposal cannot be confirmed. "
            + {
                "applied": "It has already been applied.",
                "rejected": "It was rejected.",
                "expired": "It expired — ask again and review the fresh proposal.",
                "failed": "It failed validation; its problems are in `warnings`.",
            }.get(proposal.status, "")
        )

    if not matches(proposal.proposal_sha256, proposal_sha256):
        raise ProposalError(
            "The confirmation does not match this proposal. The draft has "
            "probably changed since you saw the diff — reload it and review the "
            "new one. Confirmation binds to exact content on purpose."
        )

    proposal.status = "confirmed"
    proposal.confirmed_at = datetime.now(UTC)
    proposal.confirmed_by = scope.user_id
    await session.flush()
    return proposal


async def apply_proposal(
    session: AsyncSession, scope: EntityScope, proposal: AiProposal
) -> tuple[Invoice, list[dict[str, Any]]]:
    """
    Apply a confirmed patch to an unnumbered draft. Returns the invoice and a
    field-level audit diff.

    🔴 Idempotent, and it re-checks everything. Confirmation is not a licence:
    between confirm and apply the draft could have been issued, and this
    refuses on the invoice's *current* state rather than on what the proposal
    remembers.
    """
    _expire_if_due(proposal)

    if proposal.status == "applied":
        invoice = await session.scalar(select(Invoice).where(Invoice.id == proposal.invoice_id))
        if invoice is None:
            raise ProposalError("This proposal was applied to an invoice that no longer exists.")
        return invoice, []

    if proposal.status != "confirmed":
        raise ProposalError(
            f"A {proposal.status} proposal cannot be applied. Confirm it first — "
            f"the confirmation is what binds a person to the change."
        )

    if proposal.action not in MUTATING_ACTIONS:
        raise ProposalError(
            f"'{proposal.action}' produces an explanation, not a change. There is nothing to apply."
        )

    problems = validate_patch(proposal.proposed_patch, action=proposal.action)
    problems += await validate_references(session, scope, proposal.proposed_patch)
    if problems:
        proposal.status = "failed"
        proposal.error = "; ".join(problems)
        await session.flush()
        raise ProposalError(
            "This proposal no longer validates and was not applied: " + "; ".join(problems)
        )

    invoice: Invoice | None = None
    if proposal.invoice_id is not None:
        invoice = await session.scalar(select(Invoice).where(Invoice.id == proposal.invoice_id))
        if invoice is None:
            raise ProposalError("The draft this proposal targets no longer exists.")
        scope.check(invoice.billing_entity_id, what="invoice")
        # 🔴 Re-checked here, not trusted from the proposal.
        if invoice.status != "draft" or invoice.invoice_no:
            proposal.status = "failed"
            proposal.error = f"invoice is {invoice.status}"
            await session.flush()
            raise ProposalError(
                f"That draft has become {invoice.status}"
                + (f" (number {invoice.invoice_no})" if invoice.invoice_no else "")
                + " since the proposal was made. An issued document is never patched."
            )

        current = snapshot(invoice)
        if sha256_of(current) != sha256_of(proposal.before_snapshot):
            proposal.status = "failed"
            proposal.error = "draft changed after confirmation"
            await session.flush()
            raise ProposalError(
                "The draft changed after this proposal was confirmed, so applying it "
                "would overwrite an edit nobody reviewed. Ask again against the "
                "current draft."
            )
    else:
        entity = await session.scalar(
            select(BillingEntity).where(BillingEntity.id == proposal.billing_entity_id)
        )
        if entity is None:
            raise ProposalError("The billing entity for this proposal no longer exists.")

        patch = proposal.proposed_patch
        invoice_date = (
            date.fromisoformat(patch["invoice_date"]) if patch.get("invoice_date") else date.today()
        )
        invoice = Invoice(
            billing_entity_id=entity.id,
            entity_code=entity.code,
            template_code=entity.template_code,
            invoice_date=invoice_date,
            buyer_name=str(patch.get("buyer_name") or "(customer not set)"),
            status="draft",
            tax_treatment="igst",
            tax_rate_pct=Decimal("18.00"),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            created_by=scope.user_id,
            updated_by=scope.user_id,
        )
        session.add(invoice)
        await session.flush()

    diff = await _apply_patch(session, invoice, proposal.proposed_patch, actor=scope.user_id)

    proposal.invoice_id = invoice.id
    proposal.status = "applied"
    proposal.applied_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(invoice)
    return invoice, diff


async def _apply_patch(
    session: AsyncSession,
    invoice: Invoice,
    patch: dict[str, Any],
    *,
    actor: uuid.UUID,
) -> list[dict[str, Any]]:
    """
    Write the patch and return a field-level diff of what actually changed.

    🔴 Every amount is recomputed here through `money.py`. The patch carries
    quantity and rate; nothing else touches money, so a proposal cannot alter a
    total even by accident.
    """
    diff: list[dict[str, Any]] = []

    for key, value in patch.items():
        if key == "lines":
            continue
        if key not in PATCHABLE_HEADER_FIELDS:
            continue

        parsed: Any = value
        if key in ("invoice_date", "due_date") and value:
            parsed = date.fromisoformat(str(value))
        elif key == "organisation_id" and value:
            parsed = uuid.UUID(str(value))
        elif key == "tax_rate_pct" and value is not None:
            parsed = Decimal(str(value))
        elif key == "buyer_gstin" and value:
            parsed = str(value).strip().upper()

        before = getattr(invoice, key, None)
        if before != parsed:
            diff.append({"field": key, "before": _plain(before), "after": _plain(parsed)})
            setattr(invoice, key, parsed)

    lines = patch.get("lines")
    if lines:
        # 🔴 Queried, not read off `invoice.lines`. A create-then-apply path
        # holds a freshly flushed instance whose collection has never been
        # loaded, and touching it there is a lazy load in a context with no
        # greenlet — which surfaces as `MissingGreenlet` from inside a diff,
        # nowhere near the cause.
        existing_lines = list(
            await session.scalars(
                select(InvoiceLine)
                .where(InvoiceLine.invoice_id == invoice.id)
                .order_by(InvoiceLine.line_no)
            )
        )
        before_lines = [
            {
                "line_no": line.line_no,
                "description": line.description,
                "quantity": str(line.quantity),
                "unit": line.unit,
                "rate": str(line.rate),
                "line_total": str(line.line_total),
            }
            for line in existing_lines
        ]

        for existing in existing_lines:
            await session.delete(existing)
        await session.flush()

        taxable_supply = invoice.tax_treatment in TAXABLE_TREATMENTS
        computed = []
        after_lines = []
        for index, data in enumerate(lines, 1):
            quantity = Decimal(str(data.get("quantity") or "0"))
            rate = Decimal(str(data.get("rate") or "0"))
            unit = data.get("unit") or "each"
            inclusive = bool(data.get("rate_is_tax_inclusive"))

            amounts = compute_line(
                qty=quantity,
                rate=rate,
                tax_rate_pct=invoice.tax_rate_pct,
                rate_is_tax_inclusive=inclusive,
                taxable_supply=taxable_supply,
            )
            computed.append(amounts)
            session.add(
                InvoiceLine(
                    invoice_id=invoice.id,
                    line_no=int(data.get("line_no") or index),
                    description=str(data.get("description") or ""),
                    hsn_sac=data.get("hsn_sac"),
                    quantity=quantity,
                    unit=unit,
                    rate=rate,
                    rate_is_tax_inclusive=inclusive,
                    line_taxable_value=amounts.taxable,
                    line_tax_amount=amounts.tax,
                    line_total=amounts.total,
                    location_note=data.get("location_note"),
                )
            )
            after_lines.append(
                {
                    "line_no": int(data.get("line_no") or index),
                    "description": str(data.get("description") or ""),
                    "quantity": str(quantity),
                    "unit": unit,
                    "rate": str(rate),
                    "line_total": str(amounts.total),
                }
            )

        header = sum_lines(computed) if computed else None
        invoice.taxable_value = header.taxable if header else Decimal(0)
        invoice.tax_amount = header.tax if header else Decimal(0)
        invoice.total_value = header.total if header else Decimal(0)
        invoice.amount_in_words = rupees_in_words(invoice.total_value)

        diff.append({"field": "lines", "before": before_lines, "after": after_lines})

    invoice.updated_at = datetime.now(UTC)
    invoice.updated_by = actor
    await session.flush()
    return diff


def _plain(value: Any) -> Any:
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


async def reject_proposal(
    session: AsyncSession, scope: EntityScope, proposal: AiProposal, *, reason: str | None
) -> AiProposal:
    """Decline it. Kept, not deleted — a rejection is evidence too."""
    if proposal.status in ("applied",):
        raise ProposalError("An applied proposal cannot be rejected after the fact.")
    if proposal.status == "rejected":
        return proposal

    proposal.status = "rejected"
    proposal.rejected_at = datetime.now(UTC)
    proposal.rejection_reason = (reason or "").strip() or None
    await session.flush()
    return proposal


# ---------------------------------------------------------------------------
# "Explain this total"
# ---------------------------------------------------------------------------


def calculation_trace(invoice: Invoice) -> dict[str, Any]:
    """
    A deterministic trace of how this invoice's total was reached.

    🔴 Every figure is recomputed here, server-side. INVOICE.md §12.3 C: the
    model may paraphrase this trace and may not supply a replacement number —
    which is enforceable only because the trace exists independently of any
    model call.
    """
    taxable_supply = invoice.tax_treatment in TAXABLE_TREATMENTS
    steps: list[dict[str, Any]] = []
    computed = []

    for line in sorted(invoice.lines, key=lambda item: item.line_no):
        amounts = compute_line(
            qty=line.quantity,
            rate=line.rate,
            tax_rate_pct=invoice.tax_rate_pct,
            rate_is_tax_inclusive=line.rate_is_tax_inclusive,
            taxable_supply=taxable_supply,
        )
        computed.append(amounts)
        hectares = line.quantity_ha or to_hectares(line.quantity, line.unit)

        if line.rate_is_tax_inclusive and taxable_supply:
            explanation = (
                f"{line.quantity} {line.unit} × ₹{line.rate} = ₹{amounts.total} "
                f"including GST; back-calculated at {invoice.tax_rate_pct}% gives "
                f"₹{amounts.taxable} taxable + ₹{amounts.tax} tax"
            )
        else:
            explanation = (
                f"{line.quantity} {line.unit} × ₹{line.rate} = ₹{amounts.taxable} taxable"
                + (
                    f"; {invoice.tax_rate_pct}% = ₹{amounts.tax} tax"
                    if taxable_supply
                    else "; no tax separated out under this treatment"
                )
            )

        steps.append(
            {
                "line_no": line.line_no,
                "description": line.description,
                "quantity": str(line.quantity),
                "unit": line.unit,
                "quantity_ha": str(hectares) if hectares is not None else None,
                "rate": str(line.rate),
                "rate_is_tax_inclusive": line.rate_is_tax_inclusive,
                "taxable": str(amounts.taxable),
                "tax": str(amounts.tax),
                "total": str(amounts.total),
                "explanation": explanation,
            }
        )

    header = sum_lines(computed) if computed else None
    buyer_state = invoice.buyer_state_code or gstin_lib.state_code(invoice.buyer_gstin or "")
    supplier_state = (
        invoice.billing_entity.state_code if invoice.billing_entity is not None else "07"
    )

    return {
        "invoice_id": str(invoice.id),
        "invoice_no": invoice.invoice_no,
        "tax_treatment": invoice.tax_treatment,
        "tax_rate_pct": str(invoice.tax_rate_pct),
        "lines": steps,
        "taxable_value": str(header.taxable if header else Decimal(0)),
        "tax_amount": str(header.tax if header else Decimal(0)),
        "total_value": str(header.total if header else Decimal(0)),
        "amount_in_words": rupees_in_words(header.total if header else Decimal(0)),
        "rounding": (
            "Each line is rounded to 2 decimals and the lines are then summed. "
            "Rounding the total instead produces a figure that disagrees with the "
            "line table by a rupee (INVOICE.md §5.1)."
        ),
        "treatment_evidence": {
            "supplier_state": supplier_state,
            "buyer_state": buyer_state,
            "suggested": gstin_lib.derive_tax_treatment(supplier_state or "07", buyer_state),
            "selected": invoice.tax_treatment,
            "note": (
                "The treatment is selected by a person. It is not inferred from the "
                "state codes — INVOICE.md §5.4 is unresolved on whether some TFD "
                "billing is grant disbursement rather than taxable supply."
            ),
        },
        "header_agrees_with_lines": (
            header is not None
            and header.taxable == invoice.taxable_value
            and header.tax == invoice.tax_amount
            and header.total == invoice.total_value
        ),
    }
