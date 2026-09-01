from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from backend.schemas.organisations import PlaceOut


class FarmerRow(BaseModel):
    id: uuid.UUID
    state_id: int
    farmer_code: str | None
    first_name: str
    last_name: str | None
    district_id: int | None
    village_id: int | None
    total_area_ha: Decimal | None
    farmer_class: str
    primary_fpo_id: uuid.UUID | None
    quality_tier: str
    completeness_score: int
    theta_external_id: str | None
    is_deleted: bool
    state: PlaceOut
    district: PlaceOut | None = None

    model_config = {"from_attributes": True}


class FarmerDetail(FarmerRow):
    name_local: str | None
    father_or_spouse: str | None
    age_band: str | None
    block_id: int | None
    address_line: str | None
    pincode: str | None
    primary_crop_id: int | None
    supplying_mill_id: uuid.UUID | None
    primary_source_id: int | None
    consent_summary: dict
    owner_user_id: uuid.UUID | None
    tags: list[str]
    extra: dict
    created_at: datetime
    updated_at: datetime


class FarmerPage(BaseModel):
    count: int
    results: list[FarmerRow]


class FarmerImportResult(BaseModel):
    created: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
