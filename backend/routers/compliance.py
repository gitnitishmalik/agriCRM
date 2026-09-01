"""
`/api/v1/tax-codes/` and `/api/v1/exports/` — knowledge and accounting handoff.

🔴 Every response here says what it is not. The exports are for an accountant,
the GSTR-1 sheet is a working paper, and a knowledge record is only "verified"
when a named CA approved it. Those statements are in the payloads rather than
in documentation, because the moment somebody is about to upload a file to a
portal is the moment they are not reading documentation.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Query, Response

from backend.deps import SessionDep, StrictQuery
from backend.domain import exports as export_service
from backend.domain import knowledge as service
from backend.domain.scoping import BILLING_READ, KNOWLEDGE_APPROVE, Scope
from backend.schemas.compliance import (
    ApproveRequest,
    Gstr1WorkingPaper,
    TaxCodeOut,
    TaxCodeSuggestion,
)

router = APIRouter(prefix="/api/v1", tags=["compliance"])


@router.get(
    "/tax-codes/",
    response_model=list[TaxCodeOut],
    name="tax_code_list",
    dependencies=[StrictQuery],
)
async def list_codes(
    session: SessionDep,
    scope: Scope,
    q: str | None = Query(default=None),
    on_date: date | None = Query(default=None),
    approved_only: bool = Query(default=False),
) -> list[TaxCodeOut]:
    """
    HSN/SAC records valid on a date.

    🔴 `on_date` is the *invoice's* date when this backs a suggestion, not
    today's. A rate that changed in July does not apply to a June document, and
    the parameter exists so a caller has to decide which date it means.
    """
    scope.require(BILLING_READ, "read tax-code knowledge")
    rows = await service.search(session, query=q, on_date=on_date, approved_only=approved_only)
    return [TaxCodeOut(**service.serialise(row)) for row in rows]


@router.get(
    "/tax-codes/suggest/",
    response_model=TaxCodeSuggestion | None,
    name="tax_code_suggest",
    dependencies=[StrictQuery],
)
async def suggest_code(
    session: SessionDep,
    scope: Scope,
    description: str = Query(min_length=1, max_length=500),
    on_date: date | None = Query(default=None),
) -> TaxCodeSuggestion | None:
    """
    A code for a line description, with its effective date and citation.

    Returns null rather than a guess. A code with no source is an opinion, and
    an unreviewed record comes back labelled as such — the UI must not present
    it as a classification.
    """
    scope.require(BILLING_READ, "read tax-code knowledge")
    result = await service.suggest(
        session, description=description, on_date=on_date or date.today()
    )
    return TaxCodeSuggestion(**result) if result else None


@router.post("/tax-codes/seed/", name="tax_code_seed")
async def seed(session: SessionDep, scope: Scope) -> dict[str, int | str]:
    """
    Insert the two SACs this business bills under, if absent.

    🔴 Seeded `under_review`, never `approved`. Loading the wider rate schedule
    is a documented ingestion process with a CA at the end of it, not this
    endpoint — a scraped rate presented as verified would be this codebase
    making a tax determination.
    """
    scope.require(KNOWLEDGE_APPROVE, "seed tax-code knowledge")
    inserted = await service.seed_records(session, created_by=scope.user_id)
    return {
        "inserted": inserted,
        "review_status": "under_review",
        "note": (
            "Seeded records are unreviewed. A CA approves each one before it can "
            "be presented as verified."
        ),
    }


@router.post("/tax-codes/{record_id}/approve/", response_model=TaxCodeOut, name="tax_code_approve")
async def approve_code(
    record_id: uuid.UUID, payload: ApproveRequest, session: SessionDep, scope: Scope
) -> TaxCodeOut:
    """
    🔴 The only route to `is_verified`. Records the reviewer's name.

    The database refuses `approved` without a reviewer, so an approval cannot
    exist without somebody's name against it.
    """
    scope.require(KNOWLEDGE_APPROVE, "approve a tax-code record")
    row = await service.approve(session, scope, record_id, reviewer_name=payload.reviewer_name)
    return TaxCodeOut(**service.serialise(row))


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


@router.get("/exports/tally.csv", name="export_tally", dependencies=[StrictQuery])
async def export_tally(
    session: SessionDep,
    scope: Scope,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    entity_code: str | None = Query(default=None),
) -> Response:
    """A Tally-shaped CSV, one row per invoice line. 🔴 An export, not a filing."""
    scope.require(BILLING_READ, "export the register")
    body = await export_service.tally_csv(
        session, scope, date_from=date_from, date_to=date_to, entity_code=entity_code
    )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="tally-export.csv"',
            "X-Export-Disclaimer": export_service.DISCLAIMER,
        },
    )


@router.get("/exports/zoho.csv", name="export_zoho", dependencies=[StrictQuery])
async def export_zoho(
    session: SessionDep,
    scope: Scope,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    entity_code: str | None = Query(default=None),
) -> Response:
    scope.require(BILLING_READ, "export the register")
    body = await export_service.zoho_csv(
        session, scope, date_from=date_from, date_to=date_to, entity_code=entity_code
    )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="zoho-export.csv"',
            "X-Export-Disclaimer": export_service.DISCLAIMER,
        },
    )


@router.get(
    "/exports/gstr1-working-paper/",
    response_model=Gstr1WorkingPaper,
    name="export_gstr1_working_paper",
    dependencies=[StrictQuery],
)
async def gstr1(
    session: SessionDep,
    scope: Scope,
    date_from: date = Query(),
    date_to: date = Query(),
    entity_code: str | None = Query(default=None),
) -> Gstr1WorkingPaper:
    """
    A B2B working sheet with its reconciliation warnings.

    🔴 A working paper for the CA. It is shaped like GSTR-1's B2B table so it
    can be compared with what they file; it has not been validated against the
    portal's schema, no IRN has been obtained, and nothing here has been or
    will be submitted. The `not_a_filing` field says so in the payload.
    """
    scope.require(BILLING_READ, "produce a GSTR-1 working paper")
    return Gstr1WorkingPaper(
        **await export_service.gstr1_working_paper(
            session, scope, date_from=date_from, date_to=date_to, entity_code=entity_code
        )
    )


@router.get(
    "/invoice-ai/evaluations/summary/",
    name="ai_evaluation_summary",
    dependencies=[StrictQuery],
)
async def evaluation_summary(session: SessionDep, scope: Scope) -> dict:
    """
    Accuracy, abstention, latency and safety for the most recent run.

    🔴 `critical_passed` is reported on its own and never folded into
    `pass_rate`. An invoice number and a GSTIN are exactly right or they are
    wrong; averaging them with a dozen softer fields produces a number that
    looks healthy while the two that matter are broken.
    """
    from backend.domain import evaluation

    scope.require(BILLING_READ, "read the AI evaluation summary")

    summary = await evaluation.latest_summary(session)
    if summary is None:
        return {
            "run": None,
            "note": (
                "No evaluation run recorded. The suite runs in CI against the "
                "deterministic fake provider; see api/tests/test_evaluation.py."
            ),
        }
    return {"run": summary}
