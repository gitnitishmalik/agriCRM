"""
Creating and updating an organisation, and the duplicate gate on the way in.

🔴 Duplicate blocking is a 409, not a warning. CLAUDE.md: the admin form, the
create endpoint and `check-duplicates` all call one scorer so they cannot
drift apart — `api/dedupe.py` is that scorer, moved across unchanged.

`?force=true` overrides, and records who did it. An override that leaves no
trace is indistinguishable from the check never having run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, update

from backend.dedupe import BLOCK_THRESHOLD, find_duplicates
from backend.deps import CurrentUser, SessionDep
from backend.models.business import Organisation
from backend.schemas.organisations import (
    BulkAssignRequest,
    DuplicateCandidateOut,
    DuplicateCheckRequest,
    OrganisationCreate,
    OrganisationDetail,
    OrganisationUpdate,
)

router = APIRouter(prefix="/api/v1/organisations", tags=["organisations"])


def _candidate(candidate) -> DuplicateCandidateOut:
    org = candidate.organisation
    return DuplicateCandidateOut(
        id=org.id,
        name=org.name,
        org_code=org.org_code,
        cin=org.cin,
        district_id=org.district_id,
        score=round(candidate.score, 3),
    )


@router.post(
    "/check-duplicates/",
    response_model=list[DuplicateCandidateOut],
    name="organisation_check_duplicates",
)
async def check_duplicates(
    payload: DuplicateCheckRequest, session: SessionDep, caller: CurrentUser
) -> list[DuplicateCandidateOut]:
    """
    What creating this name would collide with.

    The same call the create path makes, exposed so a form can warn before the
    user has typed everything. Two implementations of "is this a duplicate"
    would eventually disagree, and the one the user saw would not be the one
    that blocked them.
    """
    candidates = await find_duplicates(
        session,
        payload.name,
        district_id=payload.district_id,
        state_id=payload.state_id,
        exclude_id=payload.exclude_id,
    )
    return [_candidate(c) for c in candidates]


@router.post(
    "/",
    response_model=OrganisationDetail,
    status_code=status.HTTP_201_CREATED,
    name="organisation_create",
)
async def create_organisation(
    payload: OrganisationCreate,
    session: SessionDep,
    caller: CurrentUser,
    force: bool = Query(False, description="Create despite duplicates. Recorded."),
) -> OrganisationDetail:
    """
    Add an organisation, refusing a likely duplicate.

    🔴 409 with the candidates attached, rather than a silent create. A
    register that quietly accepts the same FPO twice is a register nobody can
    count, and the second copy is discovered months later by a field agent
    visiting the same people twice.
    """
    if not force:
        candidates = await find_duplicates(
            session,
            payload.name,
            district_id=payload.district_id,
            state_id=payload.state_id,
        )
        if candidates:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "message": (
                        f"{len(candidates)} similar organisation(s) already exist at or "
                        f"above the {BLOCK_THRESHOLD} block threshold. Review them, or "
                        "resend with ?force=true to create anyway."
                    ),
                    "candidates": [_candidate(c).model_dump(mode="json") for c in candidates],
                },
            )

    organisation = Organisation(
        **payload.model_dump(exclude_unset=True),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        owner_user_id=caller.user.public_id,
        created_by=caller.user.public_id,
        updated_by=caller.user.public_id,
    )
    session.add(organisation)
    await session.flush()

    if force:
        # 🔴 Who overrode, and when. An override with no trace is the same as
        # no check having happened.
        organisation.aliases = [
            *(organisation.aliases or []),
        ]
        await _record_override(session, organisation, caller)

    await session.refresh(organisation)
    return OrganisationDetail.model_validate(organisation)


@router.put("/{organisation_id}", response_model=OrganisationDetail, name="organisation_replace")
@router.patch("/{organisation_id}", response_model=OrganisationDetail, name="organisation_update")
async def update_organisation(
    organisation_id: uuid.UUID,
    payload: OrganisationUpdate,
    session: SessionDep,
    caller: CurrentUser,
) -> OrganisationDetail:
    organisation = await session.scalar(
        select(Organisation).where(Organisation.id == organisation_id)
    )
    if organisation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such organisation.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(organisation, field, value)
    organisation.updated_at = datetime.now(UTC)
    organisation.updated_by = caller.user.public_id

    await session.flush()
    await session.refresh(organisation)
    return OrganisationDetail.model_validate(organisation)


@router.delete(
    "/{organisation_id}", status_code=status.HTTP_204_NO_CONTENT, name="organisation_delete"
)
async def delete_organisation(
    organisation_id: uuid.UUID, session: SessionDep, caller: CurrentUser
) -> None:
    """
    🔴 Soft delete. Nothing in this system is hard-deleted (CLAUDE.md).

    The row keeps its id and stays resolvable on the detail route, so a stored
    reference does not turn into a 404 that looks like the id was wrong.
    """
    organisation = await session.scalar(
        select(Organisation).where(Organisation.id == organisation_id)
    )
    if organisation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such organisation.")

    organisation.is_deleted = True
    organisation.status = "defunct"
    organisation.updated_at = datetime.now(UTC)
    organisation.updated_by = caller.user.public_id
    await session.flush()


async def _record_override(session, organisation: Organisation, caller) -> None:
    """
    Write the override to provenance, so the decision has a name against it.

    Uses `manual_entry` as the source: a human overrode a machine judgement,
    which is exactly what that source kind means.
    """
    organisation.extra = {
        **(organisation.extra or {}),
        "duplicate_override": {
            "by": str(caller.user.public_id),
            "at": datetime.now(UTC).isoformat(),
        },
    }

    from backend.models.business import FieldProvenance, Source

    source = await session.scalar(select(Source).where(Source.code == "manual_entry"))
    if source is None:
        # The register has no `manual_entry` row on this database. Not fatal —
        # the organisation is created either way — but the override is then
        # unrecorded, and silently losing that would be worse than the miss.
        return

    session.add(
        FieldProvenance(
            entity_type="organisation",
            entity_id=organisation.id,
            field_name="name",
            value_text=organisation.name,
            source_id=source.id,
            source_reference=f"duplicate check overridden by {caller.user.email}",
            confidence=0.95,
            collected_at=datetime.now(UTC),
            is_current=True,
        )
    )


@router.post("/bulk-assign/", name="organisation_bulk_assign")
async def bulk_assign(
    payload: BulkAssignRequest, session: SessionDep, caller: CurrentUser
) -> dict[str, int]:
    """
    Reassign ownership of many organisations at once.

    Live rows only — a soft-deleted record has no owner to reassign, and
    silently including tombstones would inflate the count the caller is told.
    """
    result = await session.execute(
        update(Organisation)
        .where(
            Organisation.id.in_(payload.ids),
            Organisation.is_deleted.is_(False),
        )
        .values(owner_user_id=payload.owner_user_id, updated_at=datetime.now(UTC))
        .values(updated_by=caller.user.public_id)
    )
    return {"updated": result.rowcount or 0}
