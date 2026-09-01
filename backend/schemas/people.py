"""
People, roles and contact points (Doc 11 §4).

🔴 `ContactPointOut.value` is already masked when it leaves this module. There
is no field carrying the raw value alongside it — a response shape that
included both would make the mask a suggestion to the client rather than a
control, and every `curl` would walk past it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from backend.schemas.organisations import PlaceOut

# ---------------------------------------------------------------------------
# Contact points
# ---------------------------------------------------------------------------


class ContactPointOut(BaseModel):
    """
    One phone or email.

    `masked` says which form `value` is in, so a client never has to guess
    whether it is looking at a real number — and so a screen showing an
    unmasked value can say out loud that the view was recorded.
    """

    id: uuid.UUID
    kind: str
    value: str
    masked: bool
    is_primary: bool
    verification: str
    verified_at: datetime | None = None
    delivery_failures: int
    is_whatsapp_capable: bool | None = None
    #: 🔴 True when this row belongs to a person rather than an organisation,
    #: i.e. when it is personal data under DPDP.
    is_personal: bool
    source_id: int | None = None
    created_at: datetime


class ContactPointIn(BaseModel):
    """
    A contact point being recorded.

    `source_id` is required, not optional. A phone number with no stated
    provenance cannot be assessed against R4 later, and "we do not know where
    this came from" is the state that makes a whole table unusable.
    """

    kind: str
    value: str
    is_primary: bool = False
    source_id: int
    country_code: str = "+91"


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


class RoleOut(BaseModel):
    """A post held at an organisation."""

    id: uuid.UUID
    organisation_id: uuid.UUID
    organisation_name: str | None = None
    role: str
    designation_text: str | None = None
    department: str | None = None
    is_primary_contact: bool
    is_decision_maker: bool
    valid_from: date | None = None
    valid_to: date | None = None
    is_current: bool
    source_id: int | None = None


class RoleIn(BaseModel):
    organisation_id: uuid.UUID
    role: str
    designation_text: str | None = None
    department: str | None = None
    is_primary_contact: bool = False
    is_decision_maker: bool = False
    valid_from: date | None = None
    source_id: int | None = None


class RoleCloseIn(BaseModel):
    """
    🔴 Closing a role, which is the only way a role ends.

    There is no delete endpoint for a role and this is the reason: the
    register has to answer who held a post on a past date, and a deleted row
    answers "nobody, ever".
    """

    valid_to: date


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


class PersonRow(BaseModel):
    """The list shape. Carries no contact values at all."""

    id: uuid.UUID
    full_name: str
    name_local: str | None = None
    father_or_spouse: str | None = None
    din: str | None = None
    gender: str | None = None
    quality_tier: str
    is_farmer: bool
    is_deleted: bool

    state: PlaceOut | None = None
    district: PlaceOut | None = None

    #: Populated on list responses so a client can show "MD, Kisan FPC" without
    #: a call per row.
    current_roles: list[RoleOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PersonDetail(PersonRow):
    salutation: str | None = None
    first_name: str
    middle_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    village_id: int | None = None
    primary_source_id: int | None = None
    notes: str | None = None
    extra: dict
    merged_into_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    roles: list[RoleOut] = Field(default_factory=list)
    contact_points: list[ContactPointOut] = Field(default_factory=list)


class PersonIn(BaseModel):
    """
    A person being created.

    🔴 `source_id` is required for the same reason it is on a contact point,
    and it is checked against `dq.source` before the row is written: R4 says
    personal data enters only through four named routes, and a source row is
    how the system knows which one this was.
    """

    first_name: str
    last_name: str | None = None
    middle_name: str | None = None
    salutation: str | None = None
    name_local: str | None = None
    father_or_spouse: str | None = None
    gender: str | None = None
    date_of_birth: date | None = None
    din: str | None = None
    state_id: int | None = None
    district_id: int | None = None
    village_id: int | None = None
    is_farmer: bool = False
    notes: str | None = None
    source_id: int


class PersonPatch(BaseModel):
    """Fields a caller may change. Quality tier and provenance are not among them."""

    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    salutation: str | None = None
    name_local: str | None = None
    father_or_spouse: str | None = None
    gender: str | None = None
    date_of_birth: date | None = None
    din: str | None = None
    state_id: int | None = None
    district_id: int | None = None
    village_id: int | None = None
    notes: str | None = None


class PersonPage(BaseModel):
    count: int
    results: list[PersonRow]
