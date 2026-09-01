"""Organisation shapes (Doc 11 §3)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel


class PlaceOut(BaseModel):
    """A state or district, as much of it as a client needs to render a label."""

    id: int
    lgd_code: int
    name: str
    name_local: str | None = None

    model_config = {"from_attributes": True}


class OrganisationRow(BaseModel):
    """
    The list shape. Deliberately narrower than the detail.

    State and district are nested rather than bare ids: a client showing
    "Muzaffarnagar" should not have to make two more calls to learn the name,
    and the relationships are eager-loaded on the model so this costs no extra
    query.
    """

    id: uuid.UUID
    org_code: str | None
    type: str
    status: str
    name: str
    name_local: str | None
    cin: str | None
    quality_tier: str
    member_count: int | None
    is_deleted: bool

    state: PlaceOut | None = None
    district: PlaceOut | None = None

    model_config = {"from_attributes": True}


class OrganisationDetail(OrganisationRow):
    legal_form: str
    short_name: str | None
    aliases: list[str]
    registration_no: str | None
    registration_date: date | None
    gstin: str | None
    address_line1: str | None
    address_line2: str | None
    pincode: str | None
    website: str | None
    established_year: int | None
    completeness_score: int
    merged_into_id: uuid.UUID | None
    owner_user_id: uuid.UUID | None
    extra: dict
    created_at: datetime
    updated_at: datetime


class OrganisationPage(BaseModel):
    count: int
    results: list[OrganisationRow]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


class OrganisationCreate(BaseModel):
    """
    What a caller may set when creating.

    An allow-list, not the whole model. `quality_tier`, `completeness_score`
    and `org_code` are derived or assigned by the system — accepting them here
    would let a client declare its own record Gold.
    """

    type: str
    name: str
    name_local: str | None = None
    short_name: str | None = None
    aliases: list[str] = []
    status: str = "prospect"
    legal_form: str = "unknown"
    cin: str | None = None
    registration_no: str | None = None
    registration_date: date | None = None
    gstin: str | None = None
    state_id: int | None = None
    district_id: int | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    pincode: str | None = None
    website: str | None = None
    established_year: int | None = None
    member_count: int | None = None


class OrganisationUpdate(BaseModel):
    """Every field optional — a PATCH sets only what it names."""

    name: str | None = None
    name_local: str | None = None
    short_name: str | None = None
    aliases: list[str] | None = None
    status: str | None = None
    legal_form: str | None = None
    cin: str | None = None
    registration_no: str | None = None
    registration_date: date | None = None
    gstin: str | None = None
    state_id: int | None = None
    district_id: int | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    pincode: str | None = None
    website: str | None = None
    established_year: int | None = None
    member_count: int | None = None


class DuplicateCheckRequest(BaseModel):
    name: str
    district_id: int | None = None
    state_id: int | None = None
    exclude_id: uuid.UUID | None = None


class DuplicateCandidateOut(BaseModel):
    id: uuid.UUID
    name: str
    org_code: str | None
    cin: str | None
    district_id: int | None
    score: float


class BulkAssignRequest(BaseModel):
    ids: list[uuid.UUID]
    owner_user_id: uuid.UUID
