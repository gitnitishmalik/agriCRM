from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class SourceOut(BaseModel):
    id: int
    code: str
    name: str
    kind: str
    url: str | None
    legal_basis: str
    licence: str | None
    contains_pii: bool
    is_approved: bool
    approved_by: str | None
    approved_at: datetime | None
    refresh_cadence: str | None
    notes: str | None

    model_config = {"from_attributes": True}


class ContradictionOut(BaseModel):
    id: int
    entity_type: str
    entity_id: uuid.UUID
    field_name: str
    value_a: str | None
    value_b: str | None
    detected_at: datetime
    resolved_at: datetime | None
    resolved_by: uuid.UUID | None
    resolution: str | None

    model_config = {"from_attributes": True}


class ContradictionResolution(BaseModel):
    resolution: str
