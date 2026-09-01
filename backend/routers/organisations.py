"""
Organisation registry (Doc 11 §3).

🔴 Every route depends on `CurrentUser`, which is authenticated *and* past the
second factor. That is the Phase 1 lesson carried over: the Django service
shipped a phase where the MFA permission class existed and was attached to
nothing, and every organisation endpoint served a privileged pre-MFA token.
`tests/test_mfa_boundary.py` walks this router and fails on any route that is
neither MFA-gated nor declared in `deps.PRE_MFA`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from backend.deps import CurrentUser, SessionDep, StrictQuery
from backend.models.business import Organisation
from backend.schemas.organisations import (
    OrganisationDetail,
    OrganisationPage,
    OrganisationRow,
)

router = APIRouter(prefix="/api/v1/organisations", tags=["organisations"])


@router.get(
    "/", response_model=OrganisationPage, name="organisation_list", dependencies=[StrictQuery]
)
async def list_organisations(
    session: SessionDep,
    caller: CurrentUser,
    type: str | None = None,
    status_: str | None = Query(None, alias="status"),
    state: int | None = None,
    district: int | None = None,
    quality_tier: str | None = None,
    owner: uuid.UUID | None = None,
    member_count__gte: int | None = None,
    member_count__lte: int | None = None,
    q: str | None = None,
    include_deleted: bool = False,
    limit: int = Query(50, le=200),
    offset: int = 0,
) -> OrganisationPage:
    """
    The register.

    Filters are named parameters rather than a free-form query string. FastAPI
    rejects an unknown one with a 422 by default, which preserves the rule the
    Django service enforced by hand: a typo'd filter that silently does
    nothing is how someone exports the whole registry believing they exported
    one district.
    """
    statement = select(Organisation)
    if not include_deleted:
        statement = statement.where(Organisation.is_deleted.is_(False))

    if type:
        statement = statement.where(Organisation.type == type)
    if status_:
        statement = statement.where(Organisation.status == status_)
    if state:
        statement = statement.where(Organisation.state_id == state)
    if district:
        statement = statement.where(Organisation.district_id == district)
    if quality_tier:
        statement = statement.where(Organisation.quality_tier == quality_tier)
    if owner:
        statement = statement.where(Organisation.owner_user_id == owner)
    if member_count__gte is not None:
        statement = statement.where(Organisation.member_count >= member_count__gte)
    if member_count__lte is not None:
        statement = statement.where(Organisation.member_count <= member_count__lte)
    if q:
        term = f"%{q.strip()}%"
        statement = statement.where(
            or_(
                Organisation.name.ilike(term),
                Organisation.name_local.ilike(term),
                func.array_to_string(Organisation.aliases, " | ").ilike(term),
                Organisation.cin.ilike(term),
                Organisation.org_code.ilike(term),
            )
        )

    total = await session.scalar(select(func.count()).select_from(statement.subquery()))

    rows = await session.scalars(statement.order_by(Organisation.name).limit(limit).offset(offset))

    return OrganisationPage(
        count=total or 0,
        results=[OrganisationRow.model_validate(row) for row in rows],
    )


@router.get("/{organisation_id}", response_model=OrganisationDetail, name="organisation_detail")
async def get_organisation(
    organisation_id: uuid.UUID, session: SessionDep, caller: CurrentUser
) -> OrganisationDetail:
    """
    One organisation.

    Resolves soft-deleted rows too, so a stored id never turns into a 404 that
    looks like the id was wrong — the tombstone and its state are the answer.
    """
    organisation = await session.scalar(
        select(Organisation).where(Organisation.id == organisation_id)
    )
    if organisation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such organisation.")
    return OrganisationDetail.model_validate(organisation)
