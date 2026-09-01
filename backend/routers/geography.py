"""
Reference geography (Doc 11 §4) — `ref.state`, `ref.district`, `ref.block`,
`ref.village`.

🔴 `/villages/` refuses an unscoped list. `ref.village` reaches ~660k rows once
the LGD load lands, and a naked `SELECT *` over it is a sequential scan that
blocks a connection for the length of the request. The Django service enforced
this and the rule survives the port — a list endpoint that will one day return
660,000 rows is not a list endpoint, it is an outage with pagination.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from backend.deps import CurrentUser, SessionDep, StrictQuery
from backend.models.business import Block, Crop, District, State, Village
from backend.schemas.geography import BlockOut, CropOut, DistrictOut, StateOut, VillageOut

router = APIRouter(prefix="/api/v1", tags=["geography"])


@router.get("/states/", response_model=list[StateOut], name="state_list")
async def list_states(session: SessionDep, caller: CurrentUser) -> list[StateOut]:
    """All 36 states and union territories. Small enough to return whole."""
    rows = await session.scalars(select(State).order_by(State.name))
    return [StateOut.model_validate(row) for row in rows]


@router.get(
    "/districts/",
    response_model=list[DistrictOut],
    name="district_list",
    dependencies=[StrictQuery],
)
async def list_districts(
    session: SessionDep, caller: CurrentUser, state: int | None = None
) -> list[DistrictOut]:
    """
    Districts, optionally within one state.

    ~780 rows nationally, so an unscoped list is allowed here — unlike
    villages. The `state` filter exists because that is how the UI asks.
    """
    statement = select(District).order_by(District.name)
    if state:
        statement = statement.where(District.state_id == state)
    rows = await session.scalars(statement)
    return [DistrictOut.model_validate(row) for row in rows]


@router.get(
    "/blocks/", response_model=list[BlockOut], name="block_list", dependencies=[StrictQuery]
)
async def list_blocks(
    session: SessionDep, caller: CurrentUser, district: int | None = None
) -> list[BlockOut]:
    """~7,000 blocks nationally. Scoped by district in practice."""
    statement = select(Block).order_by(Block.name)
    if district:
        statement = statement.where(Block.district_id == district)
    rows = await session.scalars(statement.limit(2000))
    return [BlockOut.model_validate(row) for row in rows]


@router.get(
    "/villages/", response_model=list[VillageOut], name="village_list", dependencies=[StrictQuery]
)
async def list_villages(
    session: SessionDep,
    caller: CurrentUser,
    district: int | None = None,
    block: int | None = None,
    pincode: str | None = None,
    q: str | None = None,
    limit: int = Query(200, le=500),
) -> list[VillageOut]:
    """
    Villages, and only within a scope.

    🔴 A district, block or pincode is required. `ref.village` reaches ~660k
    rows; refusing is the honest answer, and it is a 400 rather than an empty
    list so the caller learns what to send rather than concluding the table is
    empty.
    """
    if not any((district, block, pincode)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A district, block or pincode is required. ref.village holds roughly "
            "660,000 rows and will not be listed unscoped.",
        )

    statement = select(Village)
    if block:
        statement = statement.where(Village.block_id == block)
    if district:
        statement = statement.where(Village.district_id == district)
    if pincode:
        statement = statement.where(Village.pincode == pincode)
    if q:
        statement = statement.where(Village.name.ilike(f"%{q.strip()}%"))

    rows = await session.scalars(statement.order_by(Village.name).limit(limit))
    return [VillageOut.model_validate(row) for row in rows]


# ---------------------------------------------------------------------------
# Detail routes
#
# One per collection, because a client that stored an id has to be able to
# resolve it without listing the whole table — which for villages is the one
# thing the list route refuses to do.
# ---------------------------------------------------------------------------


@router.get("/states/{state_id}", response_model=StateOut, name="state_detail")
async def get_state(state_id: int, session: SessionDep, caller: CurrentUser) -> StateOut:
    row = await session.get(State, state_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such state.")
    return StateOut.model_validate(row)


@router.get("/districts/{district_id}", response_model=DistrictOut, name="district_detail")
async def get_district(district_id: int, session: SessionDep, caller: CurrentUser) -> DistrictOut:
    row = await session.get(District, district_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such district.")
    return DistrictOut.model_validate(row)


@router.get("/blocks/{block_id}", response_model=BlockOut, name="block_detail")
async def get_block(block_id: int, session: SessionDep, caller: CurrentUser) -> BlockOut:
    row = await session.get(Block, block_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such block.")
    return BlockOut.model_validate(row)


@router.get("/villages/{village_id}", response_model=VillageOut, name="village_detail")
async def get_village(village_id: int, session: SessionDep, caller: CurrentUser) -> VillageOut:
    """
    One village by id.

    Unlike the list route this needs no scope — resolving a single stored id is
    an index lookup, not the sequential scan the list guard exists to prevent.
    """
    row = await session.get(Village, village_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such village.")
    return VillageOut.model_validate(row)


@router.get("/crops/", response_model=list[CropOut], name="crop_list")
async def list_crops(session: SessionDep, caller: CurrentUser) -> list[CropOut]:
    """Reference crops. A short list; returned whole."""
    rows = await session.scalars(select(Crop).order_by(Crop.name))
    return [CropOut.model_validate(row) for row in rows]


@router.get("/crops/{crop_id}", response_model=CropOut, name="crop_detail")
async def get_crop(crop_id: int, session: SessionDep, caller: CurrentUser) -> CropOut:
    row = await session.get(Crop, crop_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such crop.")
    return CropOut.model_validate(row)
