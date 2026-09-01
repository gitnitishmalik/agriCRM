from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from backend.deps import CurrentUser, SessionDep
from backend.models.business import Contradiction, Source
from backend.schemas.dataquality import ContradictionOut, ContradictionResolution, SourceOut

router = APIRouter(prefix="/api/v1/data-quality", tags=["data-quality"])
ADMIN_ROLES = frozenset({"data_ops", "compliance", "admin"})


def _require_data_admin(caller) -> None:
    if caller.user.role not in ADMIN_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Data-quality administration role required.")


@router.get("/sources/", response_model=list[SourceOut], name="source_list")
async def list_sources(session: SessionDep, caller: CurrentUser) -> list[SourceOut]:
    _require_data_admin(caller)
    rows = await session.scalars(select(Source).order_by(Source.code))
    return [SourceOut.model_validate(row) for row in rows]


@router.post("/sources/{source_id}/approve/", response_model=SourceOut, name="source_approve")
async def approve_source(source_id: int, session: SessionDep, caller: CurrentUser) -> SourceOut:
    if caller.user.role not in {"compliance", "admin"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Compliance or admin approval required.")
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such source.")
    if not source.legal_basis.strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "A written legal basis is required.")
    source.is_approved = True
    source.approved_by = caller.user.email
    source.approved_at = datetime.now(UTC)
    await session.flush()
    return SourceOut.model_validate(source)


@router.get("/contradictions/", response_model=list[ContradictionOut], name="contradiction_list")
async def list_contradictions(
    session: SessionDep,
    caller: CurrentUser,
    open_only: bool = True,
    limit: int = Query(100, le=500),
) -> list[ContradictionOut]:
    _require_data_admin(caller)
    statement = select(Contradiction)
    if open_only:
        statement = statement.where(Contradiction.resolved_at.is_(None))
    rows = await session.scalars(statement.order_by(Contradiction.detected_at.desc()).limit(limit))
    return [ContradictionOut.model_validate(row) for row in rows]


@router.post(
    "/contradictions/{contradiction_id}/resolve/",
    response_model=ContradictionOut,
    name="contradiction_resolve",
)
async def resolve_contradiction(
    contradiction_id: int,
    payload: ContradictionResolution,
    session: SessionDep,
    caller: CurrentUser,
) -> ContradictionOut:
    _require_data_admin(caller)
    row = await session.get(Contradiction, contradiction_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such contradiction.")
    if row.resolved_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Contradiction already resolved.")
    if not payload.resolution.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Resolution is required.")
    row.resolution = payload.resolution.strip()
    row.resolved_at = datetime.now(UTC)
    row.resolved_by = caller.user.public_id
    await session.flush()
    return ContradictionOut.model_validate(row)
