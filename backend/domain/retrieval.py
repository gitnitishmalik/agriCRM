"""
Read-only CRM retrieval for the copilot.

🔴 **Every function here takes an `EntityScope` and filters on it.** That is
not defensive style — it is the tenant boundary. A retrieval helper that
accepts an organisation id and returns the row is a cross-tenant read waiting
for a caller who passes one in from a request body.

🔴 **There is no generic "run tool" entry point** (INVOICE.md §12.6). The
functions in this module are the entire vocabulary; a server-side allow-list
decides which copilot actions exist, and each of them calls specific functions
here rather than dispatching a name the model chose.

Everything returned is plain data with an `evidence` shape — an id, a label and
enough to render a link — so the copilot panel can show *why* it suggested
something rather than asserting it.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.scoping import EntityScope
from backend.models.billing import BillingEntity, Invoice
from backend.models.business import Organisation
from backend.models.invoice_ops import ContractRate, TaxCodeKnowledge

#: A cap on everything. A retrieval that can return the whole registry is a
#: retrieval that will eventually be asked to.
MAX_ROWS = 25


def _evidence(kind: str, identifier: Any, label: str, **extra: Any) -> dict[str, Any]:
    """One citation, in the shape the copilot panel renders."""
    return {"kind": kind, "id": str(identifier), "label": label, **extra}


async def find_organisations(
    session: AsyncSession,
    scope: EntityScope,
    *,
    query: str | None = None,
    limit: int = MAX_ROWS,
) -> list[dict[str, Any]]:
    """
    Customers the caller may bill, optionally narrowed by name.

    Deliberately returns *candidates*, not a match. INVOICE.md §12.3 A: where
    two organisations are plausible, both are shown — Syngenta holds a separate
    GSTIN per state and picking one silently is how an invoice goes out against
    the wrong registration.
    """
    conditions = [Organisation.is_deleted.is_(False)]

    if query and query.strip():
        pattern = f"%{query.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(Organisation.name).like(pattern),
                func.lower(func.coalesce(Organisation.short_name, "")).like(pattern),
            )
        )

    rows = list(
        await session.scalars(
            select(Organisation)
            .where(and_(*conditions))
            .order_by(Organisation.name)
            .limit(min(limit, MAX_ROWS))
        )
    )

    return [
        {
            "id": str(org.id),
            "name": org.name,
            "short_name": org.short_name,
            "gstin": org.gstin,
            "org_type": org.type,
            "state_id": org.state_id,
            "district_id": org.district_id,
            "quality_tier": org.quality_tier,
            "billing_email": org.billing_email,
            "billing_opt_out": org.billing_opt_out,
            "evidence": _evidence("organisation", org.id, org.name, gstin=org.gstin),
        }
        for org in rows
    ]


async def find_billing_entity(
    session: AsyncSession, scope: EntityScope, *, code: str | None = None
) -> dict[str, Any] | None:
    """
    The issuing company, current as of today.

    🔴 Scoped. An entity outside the caller's scope returns None rather than a
    row, so a proposal cannot be built against a company they cannot bill for.
    """
    conditions = [BillingEntity.id.in_(scope.entity_ids), BillingEntity.valid_to.is_(None)]
    if code:
        conditions.append(BillingEntity.code == code.upper())

    entity = await session.scalar(select(BillingEntity).where(and_(*conditions)).limit(1))
    if entity is None:
        return None

    return {
        "id": str(entity.id),
        "code": entity.code,
        "legal_name": entity.legal_name,
        "state_code": entity.state_code,
        "gstin": entity.gstin,
        "template_code": entity.template_code,
        "evidence": _evidence("billing_entity", entity.id, entity.legal_name),
    }


async def find_contract_rate(
    session: AsyncSession,
    scope: EntityScope,
    *,
    organisation_id: uuid.UUID | None,
    on_date: date,
    unit: str | None = None,
    buyer_order_no: str | None = None,
) -> dict[str, Any] | None:
    """
    The agreed rate for this customer on this date, if one is on file.

    Effective-dated, like everything else that changes: a rate agreed in April
    and superseded in July is the April rate for an April invoice.
    """
    if organisation_id is None:
        return None

    conditions = [
        ContractRate.billing_entity_id.in_(scope.entity_ids),
        ContractRate.organisation_id == organisation_id,
        ContractRate.valid_from <= on_date,
        or_(ContractRate.valid_to.is_(None), ContractRate.valid_to >= on_date),
    ]
    if unit:
        conditions.append(ContractRate.unit == unit)
    if buyer_order_no:
        conditions.append(
            or_(
                ContractRate.buyer_order_no.is_(None),
                ContractRate.buyer_order_no == buyer_order_no,
            )
        )

    rate = await session.scalar(
        select(ContractRate)
        .where(and_(*conditions))
        .order_by(ContractRate.valid_from.desc())
        .limit(1)
    )
    if rate is None:
        return None

    return {
        "id": str(rate.id),
        "rate": rate.rate,
        "unit": rate.unit,
        "hsn_sac": rate.hsn_sac,
        "rate_is_tax_inclusive": rate.rate_is_tax_inclusive,
        "tolerance_pct": rate.tolerance_pct,
        "valid_from": rate.valid_from.isoformat(),
        "source_reference": rate.source_reference,
        "evidence": _evidence(
            "contract_rate",
            rate.id,
            f"{rate.rate} per {rate.unit}"
            + (f" — {rate.source_reference}" if rate.source_reference else ""),
            valid_from=rate.valid_from.isoformat(),
        ),
    }


async def recent_invoices(
    session: AsyncSession,
    scope: EntityScope,
    *,
    organisation_id: uuid.UUID | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    What this customer was billed recently.

    Two uses: the copilot proposes a line resembling last time, and the
    duplicate check has somewhere to point when the amounts match.
    """
    conditions = [
        Invoice.billing_entity_id.in_(scope.entity_ids),
        Invoice.is_deleted.is_(False),
    ]
    if organisation_id is not None:
        conditions.append(Invoice.organisation_id == organisation_id)

    rows = list(
        await session.scalars(
            select(Invoice)
            .where(and_(*conditions))
            .order_by(Invoice.invoice_date.desc())
            .limit(min(limit, MAX_ROWS))
        )
    )

    return [
        {
            "id": str(inv.id),
            "invoice_no": inv.invoice_no,
            "invoice_date": inv.invoice_date.isoformat(),
            "status": inv.status,
            "total_value": inv.total_value,
            "tax_treatment": inv.tax_treatment,
            "buyer_order_no": inv.buyer_order_no,
            "lines": [
                {
                    "description": line.description,
                    "hsn_sac": line.hsn_sac,
                    "quantity": line.quantity,
                    "unit": line.unit,
                    "rate": line.rate,
                    "rate_is_tax_inclusive": line.rate_is_tax_inclusive,
                }
                for line in inv.lines
            ],
            "evidence": _evidence(
                "invoice",
                inv.id,
                f"{inv.invoice_no or 'draft'} · {inv.invoice_date.isoformat()}",
                total=str(inv.total_value),
            ),
        }
        for inv in rows
    ]


async def suggest_tax_code(
    session: AsyncSession,
    *,
    description: str,
    on_date: date,
) -> dict[str, Any] | None:
    """
    An HSN/SAC suggestion valid on the invoice's date.

    🔴 Retrieval is by the *invoice's* date, not today's — a rate that changed
    in July does not apply to a June document. And an unapproved row comes back
    labelled `review_status: ai_suggested`; the UI must not present it as
    verified, and `is_approved` is what it checks.
    """
    text = (description or "").strip().lower()
    if not text:
        return None

    rows = list(
        await session.scalars(
            select(TaxCodeKnowledge)
            .where(
                TaxCodeKnowledge.effective_from <= on_date,
                or_(
                    TaxCodeKnowledge.effective_to.is_(None),
                    TaxCodeKnowledge.effective_to >= on_date,
                ),
            )
            .order_by(TaxCodeKnowledge.effective_from.desc())
        )
    )

    for row in rows:
        haystack = [row.description.lower(), *(k.lower() for k in row.keywords)]
        if any(word and word in text for word in haystack):
            return {
                "code": row.code,
                "code_kind": row.code_kind,
                "description": row.description,
                "gst_rate_pct": row.gst_rate_pct,
                "effective_from": row.effective_from.isoformat(),
                "effective_to": row.effective_to.isoformat() if row.effective_to else None,
                "review_status": row.review_status,
                "is_approved": row.is_approved,
                "citation": {
                    "title": row.source_title,
                    "url": row.source_url,
                    "reviewed_by": row.reviewer_name,
                    "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
                },
                "evidence": _evidence(
                    "tax_code",
                    row.id,
                    f"{row.code} — {row.description}",
                    effective_from=row.effective_from.isoformat(),
                    review_status=row.review_status,
                ),
            }
    return None


async def build_context(
    session: AsyncSession,
    scope: EntityScope,
    *,
    request_text: str,
    organisation_id: uuid.UUID | None = None,
    on_date: date | None = None,
) -> dict[str, Any]:
    """
    Assemble everything a provider may see for one request.

    🔴 Assembled here rather than by the provider, so the provider cannot widen
    its own view. It receives a dictionary; it has no session and no way to ask
    a second question.
    """
    on_date = on_date or date.today()

    organisations = await find_organisations(session, scope, query=None, limit=MAX_ROWS)
    contract = await find_contract_rate(
        session, scope, organisation_id=organisation_id, on_date=on_date
    )
    invoices = await recent_invoices(session, scope, organisation_id=organisation_id, limit=5)

    return {
        "organisations": organisations,
        "contract_rate": contract,
        "recent_invoices": invoices,
        "today": on_date.isoformat(),
        "window_days": (timedelta(days=45)).days,
    }
