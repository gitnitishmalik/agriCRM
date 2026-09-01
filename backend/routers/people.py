"""
People, their posts, and how to reach them (Doc 11 §4). Phase 1, sprint 3.

🔴 This is the module where personal data lives, so three controls sit on
every route rather than on a screen that remembers to ask:

  * **R4 at the door.** A person or contact point names the `dq.source` row it
    arrived through, and `domain.pii.require_pii_source` refuses a source
    whose kind may carry institutional facts but not personal data. An
    institutional collector can write a director's name and DIN — both
    published by the MCA under statute — and cannot write their mobile.
  * **R9 by default.** Contact values leave here masked. Unmasking is a query
    parameter, needs `contact.view_full`, and writes `audit.data_access_log`
    before the response is built.
  * **R10 on volume.** A read returning more than `BULK_PII_THRESHOLD`
    personal records needs a typed reason and raises an alert.

The list endpoint deliberately carries no contact values at all. A directory
of names is a working tool; a directory of mobile numbers is the thing that
gets exported once and lives on a laptop forever.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from backend.deps import CurrentUser, SessionDep, StrictQuery
from backend.domain import pii
from backend.models.business import ContactPoint, Organisation, Person, PersonOrgRole
from backend.schemas.people import (
    ContactPointIn,
    ContactPointOut,
    PersonDetail,
    PersonIn,
    PersonPage,
    PersonPatch,
    PersonRow,
    RoleCloseIn,
    RoleIn,
    RoleOut,
)

router = APIRouter(prefix="/api/v1/people", tags=["people"])


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _role_out(role: PersonOrgRole) -> RoleOut:
    return RoleOut(
        id=role.id,
        organisation_id=role.organisation_id,
        organisation_name=role.organisation.name if role.organisation else None,
        role=role.role,
        designation_text=role.designation_text,
        department=role.department,
        is_primary_contact=role.is_primary_contact,
        is_decision_maker=role.is_decision_maker,
        valid_from=role.valid_from,
        valid_to=role.valid_to,
        is_current=role.is_current,
        source_id=role.source_id,
    )


def _contact_out(point: ContactPoint, *, unmask: bool) -> ContactPointOut:
    """
    🔴 The masking decision is made here and nowhere else.

    An organisation's switchboard is not personal data and is never masked; a
    person's number is, unless the caller both holds the capability and asked
    for it. The `masked` flag tells the client which it is looking at, so a
    screen can say plainly that an unmasked view was recorded.
    """
    personal = point.is_personal
    show_full = unmask or not personal
    return ContactPointOut(
        id=point.id,
        kind=point.kind,
        value=point.value_normalised
        if show_full
        else pii.mask_value(point.kind, point.value_normalised),
        masked=not show_full,
        is_primary=point.is_primary,
        verification=point.verification,
        verified_at=point.verified_at,
        delivery_failures=point.delivery_failures,
        is_whatsapp_capable=point.is_whatsapp_capable,
        is_personal=personal,
        source_id=point.source_id,
        created_at=point.created_at,
    )


async def _load_person(session: SessionDep, person_id: uuid.UUID) -> Person:
    person = await session.scalar(select(Person).where(Person.id == person_id))
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person.")
    return person


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


# 🔴 Both forms registered, neither redirecting. FastAPI answers a
# trailing-slash mismatch with a 307 to an absolute URL on the backend origin;
# behind the dev proxy that is cross-origin, and browsers drop `Authorization`
# across origins — so the retry arrives unauthenticated and the client loops on
# token refresh. Same pattern as `healthz` / `healthz_alias` in `main.py`.
@router.get("/", response_model=PersonPage, name="person_list", dependencies=[StrictQuery])
@router.get(
    "",
    response_model=PersonPage,
    name="person_list_alias",
    include_in_schema=False,
    dependencies=[StrictQuery],
)
async def list_people(
    request: Request,
    session: SessionDep,
    caller: CurrentUser,
    organisation: uuid.UUID | None = None,
    role: str | None = None,
    state: int | None = None,
    district: int | None = None,
    quality_tier: str | None = None,
    is_farmer: bool | None = None,
    din: str | None = None,
    current_roles_only: bool = True,
    q: str | None = None,
    include_deleted: bool = False,
    reason: str | None = Query(
        None,
        description=(
            "Required (R10) when the result exceeds the bulk threshold. "
            "Recorded against your account in audit.data_access_log."
        ),
    ),
    limit: int = Query(50, le=200),
    offset: int = 0,
) -> PersonPage:
    """
    The people register. **Names and posts only — no contact values.**

    Filters are declared parameters and `StrictQuery` rejects anything else
    with a 400: a typo'd filter that silently does nothing is how someone
    exports the whole register believing they exported one district.
    """
    statement = select(Person)

    if not include_deleted:
        statement = statement.where(Person.is_deleted.is_(False))
    if state:
        statement = statement.where(Person.state_id == state)
    if district:
        statement = statement.where(Person.district_id == district)
    if quality_tier:
        statement = statement.where(Person.quality_tier == quality_tier)
    if is_farmer is not None:
        statement = statement.where(Person.is_farmer.is_(is_farmer))
    if din:
        statement = statement.where(Person.din == din)
    if q:
        term = f"%{q.strip()}%"
        statement = statement.where(
            or_(
                Person.full_name.ilike(term),
                Person.name_local.ilike(term),
                Person.father_or_spouse.ilike(term),
            )
        )

    if organisation or role:
        joined = select(PersonOrgRole.person_id)
        if organisation:
            joined = joined.where(PersonOrgRole.organisation_id == organisation)
        if role:
            joined = joined.where(PersonOrgRole.role == role)
        if current_roles_only:
            joined = joined.where(PersonOrgRole.valid_to.is_(None))
        statement = statement.where(Person.id.in_(joined))

    total = await session.scalar(select(func.count()).select_from(statement.subquery()))

    people = list(
        await session.scalars(statement.order_by(Person.full_name).limit(limit).offset(offset))
    )

    # 🔴 R10 counts rows returned, not rows matched: a paginated walk is the
    # shape an exfiltration takes, and each page is its own logged read.
    stated_reason = pii.check_bulk_reason(len(people), reason)

    roles_by_person: dict[uuid.UUID, list[RoleOut]] = {}
    if people:
        role_rows = await session.scalars(
            select(PersonOrgRole)
            .where(PersonOrgRole.person_id.in_([p.id for p in people]))
            .where(PersonOrgRole.valid_to.is_(None))
        )
        for role_row in role_rows:
            roles_by_person.setdefault(role_row.person_id, []).append(_role_out(role_row))

    await pii.log_access(
        session,
        caller=caller,
        action="search",
        entity_type="core.person",
        record_count=len(people),
        filters={
            "organisation": organisation,
            "role": role,
            "state": state,
            "district": district,
            "q": q,
        },
        reason=stated_reason,
        ip_address=pii.actor_ip(request),
    )

    return PersonPage(
        count=total or 0,
        results=[
            PersonRow.model_validate(person).model_copy(
                update={"current_roles": roles_by_person.get(person.id, [])}
            )
            for person in people
        ],
    )


@router.get("/{person_id}", response_model=PersonDetail, name="person_detail")
async def get_person(
    person_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    caller: CurrentUser,
    unmask: bool = Query(
        False,
        description=(
            "Show full contact values. Requires the contact.view_full "
            "capability (R9); the view is written to audit.data_access_log."
        ),
    ),
) -> PersonDetail:
    """
    One person, with every post they have held and every way to reach them.

    🔴 `unmask=true` is checked *before* the response is assembled and is
    logged whether or not the person turns out to have any contact points. A
    caller who asks to see personal data has asked, and that is the fact the
    audit log records.
    """
    person = await _load_person(session, person_id)

    if unmask:
        pii.require_unmask(caller)

    roles = list(
        await session.scalars(
            select(PersonOrgRole)
            .where(PersonOrgRole.person_id == person_id)
            .order_by(PersonOrgRole.valid_to.is_(None).desc(), PersonOrgRole.valid_from.desc())
        )
    )
    points = list(
        await session.scalars(
            select(ContactPoint)
            .where(ContactPoint.person_id == person_id)
            .order_by(ContactPoint.is_primary.desc(), ContactPoint.created_at)
        )
    )

    await pii.log_access(
        session,
        caller=caller,
        action="view_pii" if unmask else "view",
        entity_type="core.person",
        record_count=1,
        filters={"person_id": str(person_id), "unmasked": unmask},
        ip_address=pii.actor_ip(request),
    )

    detail = PersonDetail.model_validate(person)
    return detail.model_copy(
        update={
            "roles": [_role_out(role) for role in roles],
            "current_roles": [_role_out(role) for role in roles if role.is_current],
            "contact_points": [_contact_out(point, unmask=unmask) for point in points],
        }
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


@router.post(
    "/", response_model=PersonDetail, status_code=status.HTTP_201_CREATED, name="person_create"
)
async def create_person(
    payload: PersonIn, request: Request, session: SessionDep, caller: CurrentUser
) -> PersonDetail:
    """
    Record a person.

    🔴 The `source_id` gate is the whole point of this endpoint. R4 permits
    personal data through four routes, and `require_pii_source` is where that
    sentence becomes something a request either passes or fails.
    """
    await pii.require_pii_source(session, payload.source_id)

    now = datetime.now(UTC)
    person = Person(
        **payload.model_dump(exclude={"source_id"}),
        primary_source_id=payload.source_id,
        created_at=now,
        updated_at=now,
        created_by=caller.user.public_id,
        updated_by=caller.user.public_id,
    )
    session.add(person)
    try:
        await session.flush()
    except IntegrityError as error:
        # The DDL's partial unique index on `din`. A DIN identifies exactly one
        # director at the MCA, so two rows holding one is a duplicate person,
        # not two people.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A person already holds DIN {payload.din}. Merge the records "
            f"rather than creating a second.",
        ) from error

    await session.refresh(person)
    await pii.log_access(
        session,
        caller=caller,
        action="create",
        entity_type="core.person",
        record_count=1,
        filters={"source_id": payload.source_id},
        ip_address=pii.actor_ip(request),
    )
    return PersonDetail.model_validate(person)


@router.patch("/{person_id}", response_model=PersonDetail, name="person_update")
async def update_person(
    person_id: uuid.UUID, payload: PersonPatch, session: SessionDep, caller: CurrentUser
) -> PersonDetail:
    """Correct a person's details. Provenance and tier are not editable here."""
    person = await _load_person(session, person_id)

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update.")

    for field, value in changes.items():
        setattr(person, field, value)
    person.updated_at = datetime.now(UTC)
    person.updated_by = caller.user.public_id
    await session.flush()
    await session.refresh(person)
    return PersonDetail.model_validate(person)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


@router.post(
    "/{person_id}/roles",
    response_model=RoleOut,
    status_code=status.HTTP_201_CREATED,
    name="person_role_create",
)
async def add_role(
    person_id: uuid.UUID, payload: RoleIn, session: SessionDep, caller: CurrentUser
) -> RoleOut:
    """
    Record that this person holds a post at an organisation.

    🔴 An open role is not replaced by a new one — both are written, and the
    old one is closed through `PATCH .../roles/{id}` with a date. That is what
    lets the register answer "who was chairman in March 2025" afterwards.
    """
    await _load_person(session, person_id)

    organisation = await session.get(Organisation, payload.organisation_id)
    if organisation is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No such organisation.")

    role = PersonOrgRole(
        person_id=person_id,
        created_at=datetime.now(UTC),
        **payload.model_dump(),
    )
    session.add(role)
    try:
        await session.flush()
    except IntegrityError as error:
        # `uq_por_primary`: one open primary contact per organisation. Raised
        # by the database rather than pre-checked here — a read-then-write
        # check races, and the index does not.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{organisation.name} already has an open primary contact. Close "
            f"that role with an end date before naming a new one.",
        ) from error

    await session.refresh(role)
    return _role_out(role)


@router.patch("/{person_id}/roles/{role_id}", response_model=RoleOut, name="person_role_close")
async def close_role(
    person_id: uuid.UUID,
    role_id: uuid.UUID,
    payload: RoleCloseIn,
    session: SessionDep,
    caller: CurrentUser,
) -> RoleOut:
    """
    End a post by dating it. There is no delete.

    The DDL's CHECK requires `valid_to >= valid_from`; a date before the start
    is rejected there rather than re-implemented here.
    """
    role = await session.scalar(
        select(PersonOrgRole).where(
            PersonOrgRole.id == role_id, PersonOrgRole.person_id == person_id
        )
    )
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such role for this person.")
    if role.valid_to is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"That role already ended on {role.valid_to.isoformat()}.",
        )

    role.valid_to = payload.valid_to
    role.is_primary_contact = False
    try:
        await session.flush()
    except IntegrityError as error:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The end date is before the start date.",
        ) from error
    await session.refresh(role)
    return _role_out(role)


# ---------------------------------------------------------------------------
# Contact points
# ---------------------------------------------------------------------------


@router.post(
    "/{person_id}/contact-points",
    response_model=ContactPointOut,
    status_code=status.HTTP_201_CREATED,
    name="person_contact_create",
)
async def add_contact_point(
    person_id: uuid.UUID,
    payload: ContactPointIn,
    request: Request,
    session: SessionDep,
    caller: CurrentUser,
) -> ContactPointOut:
    """
    Record a way to reach this person.

    🔴 Two gates, both R4. The source must be one personal data may arrive
    through, *and* — because this is a named individual's number rather than
    an office line — the check is not skippable by writing the row against the
    organisation instead: that path is a different endpoint with a different
    meaning.

    The value is normalised to E.164 before storage and a value that will not
    normalise is rejected rather than guessed at. A number stored wrong
    surfaces months later as an undeliverable message counted against the
    WhatsApp quality rating, by which time nobody can tell which import did it.
    """
    await _load_person(session, person_id)
    await pii.require_pii_source(session, payload.source_id)

    try:
        normalised = pii.normalise(payload.kind, payload.value, country_code=payload.country_code)
    except pii.NormalisationError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    now = datetime.now(UTC)
    point = ContactPoint(
        person_id=person_id,
        kind=payload.kind,
        value_raw=payload.value,
        value_normalised=normalised,
        country_code=payload.country_code,
        is_primary=payload.is_primary,
        verification="unverified",
        is_whatsapp_capable=(pii.is_indian_mobile(normalised) if payload.kind != "email" else None),
        source_id=payload.source_id,
        created_at=now,
        updated_at=now,
    )
    session.add(point)
    try:
        await session.flush()
    except IntegrityError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That contact point is already recorded against this person.",
        ) from error

    await session.refresh(point)
    await pii.log_access(
        session,
        caller=caller,
        action="create",
        entity_type="core.contact_point",
        record_count=1,
        filters={"person_id": str(person_id), "kind": payload.kind},
        ip_address=pii.actor_ip(request),
    )
    # Echoed masked. 🔴 The caller supplied the value, so this hides nothing
    # from them — it keeps the response shape identical to every other read,
    # so a client never has one code path that expects a raw value.
    return _contact_out(point, unmask=False)


@router.get(
    "/{person_id}/contact-points",
    response_model=list[ContactPointOut],
    name="person_contact_list",
    dependencies=[StrictQuery],
)
async def list_contact_points(
    person_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    caller: CurrentUser,
    unmask: bool = False,
) -> list[ContactPointOut]:
    """This person's contact points, masked unless the capability says otherwise."""
    await _load_person(session, person_id)
    if unmask:
        pii.require_unmask(caller)

    points = list(
        await session.scalars(
            select(ContactPoint)
            .where(ContactPoint.person_id == person_id)
            .order_by(ContactPoint.is_primary.desc(), ContactPoint.created_at)
        )
    )
    await pii.log_access(
        session,
        caller=caller,
        action="view_pii" if unmask else "view",
        entity_type="core.contact_point",
        record_count=len(points),
        filters={"person_id": str(person_id), "unmasked": unmask},
        ip_address=pii.actor_ip(request),
    )
    return [_contact_out(point, unmask=unmask) for point in points]


@router.get(
    "/by-organisation/{organisation_id}",
    response_model=list[RoleOut],
    name="organisation_people",
    dependencies=[StrictQuery],
)
async def people_at_organisation(
    organisation_id: uuid.UUID,
    session: SessionDep,
    caller: CurrentUser,
    current_only: bool = True,
) -> list[RoleOut]:
    """
    Who holds which post at this organisation — the board, in other words.

    No contact values. This is the endpoint a BD screen calls to show "MD:
    Sunita Devi", and it is deliberately not the endpoint that would hand a
    client every director's mobile in one response.
    """
    if await session.get(Organisation, organisation_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such organisation.")

    statement = select(PersonOrgRole).where(PersonOrgRole.organisation_id == organisation_id)
    if current_only:
        statement = statement.where(PersonOrgRole.valid_to.is_(None))

    roles = await session.scalars(statement.order_by(PersonOrgRole.is_decision_maker.desc()))
    return [_role_out(role) for role in roles]


__all__ = ["router"]
