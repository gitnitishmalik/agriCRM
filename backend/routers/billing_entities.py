"""
Billing entities — the companies that issue invoices.

🔴 Read-only over the API, exactly as under Django. Editing an entity is a
versioning operation, not a field update: the bank details printed on a 2025
invoice must stay what they were, so a change means closing the current row
(`valid_to`) and opening a new one. That is done deliberately, in an admin
surface where the consequence is visible — not by a PATCH.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from backend.deps import CurrentUser, SessionDep, StrictQuery
from backend.models.billing import BillingEntity
from backend.schemas.billing import BillingEntityOut

router = APIRouter(prefix="/api/v1/billing-entities", tags=["billing"])


@router.get(
    "/",
    response_model=list[BillingEntityOut],
    name="billing_entity_list",
    dependencies=[StrictQuery],
)
async def list_entities(
    session: SessionDep, caller: CurrentUser, current: bool = False
) -> list[BillingEntityOut]:
    """`?current=true` returns only the open row per entity."""
    statement = select(BillingEntity)
    if current:
        statement = statement.where(BillingEntity.valid_to.is_(None))
    rows = await session.scalars(
        statement.order_by(BillingEntity.code, BillingEntity.valid_from.desc())
    )
    return [BillingEntityOut.model_validate(row) for row in rows]


@router.get("/{entity_id}", response_model=BillingEntityOut, name="billing_entity_detail")
async def get_entity(
    entity_id: uuid.UUID, session: SessionDep, caller: CurrentUser
) -> BillingEntityOut:
    row = await session.get(BillingEntity, entity_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such billing entity.")
    return BillingEntityOut.model_validate(row)
