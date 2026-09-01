"""Reference geography shapes (Doc 11 §4)."""

from __future__ import annotations

from pydantic import BaseModel


class StateOut(BaseModel):
    id: int
    lgd_code: int
    name: str
    name_local: str | None = None
    iso_code: str | None = None

    model_config = {"from_attributes": True}


class DistrictOut(BaseModel):
    id: int
    lgd_code: int
    state_id: int
    name: str
    name_local: str | None = None

    model_config = {"from_attributes": True}


class BlockOut(BaseModel):
    id: int
    lgd_code: int | None = None
    district_id: int
    name: str
    name_local: str | None = None

    model_config = {"from_attributes": True}


class VillageOut(BaseModel):
    id: int
    lgd_code: int | None = None
    block_id: int | None = None
    district_id: int | None = None
    name: str
    name_local: str | None = None
    pincode: str | None = None

    model_config = {"from_attributes": True}


class CropOut(BaseModel):
    id: int
    code: str
    name: str
    name_local: str | None = None
    category: str | None = None
    default_season: str | None = None

    model_config = {"from_attributes": True}
