from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select

from backend.deps import CurrentUser, SessionDep, StrictQuery
from backend.models.business import Farmer, FieldProvenance, Source, State
from backend.schemas.farmers import FarmerDetail, FarmerImportResult, FarmerPage, FarmerRow

router = APIRouter(prefix="/api/v1/farmers", tags=["farmers"])

PII_IMPORT_KINDS = frozenset(
    {
        "partner_agreement",
        "field_collection",
        "inbound_signup",
        "theta_analytics",
        "purchased_licensed",
    }
)
IMPORT_ROLES = frozenset({"data_ops", "compliance", "admin"})
MAX_IMPORT_BYTES = 10 * 1024 * 1024


def _territory(statement, caller):
    if caller.user.is_cross_territory:
        return statement
    districts = caller.user.district_ids or []
    if not districts:
        return statement.where(False)
    return statement.where(Farmer.district_id.in_(districts))


@router.get("/", response_model=FarmerPage, name="farmer_list", dependencies=[StrictQuery])
async def list_farmers(
    session: SessionDep,
    caller: CurrentUser,
    state: int = Query(..., description="Required partition key"),
    district: int | None = None,
    primary_fpo: uuid.UUID | None = None,
    quality_tier: str | None = None,
    q: str | None = None,
    include_deleted: bool = False,
    limit: int = Query(50, le=200),
    offset: int = 0,
) -> FarmerPage:
    statement = _territory(select(Farmer).where(Farmer.state_id == state), caller)
    if not include_deleted:
        statement = statement.where(Farmer.is_deleted.is_(False))
    if district is not None:
        statement = statement.where(Farmer.district_id == district)
    if primary_fpo is not None:
        statement = statement.where(Farmer.primary_fpo_id == primary_fpo)
    if quality_tier is not None:
        statement = statement.where(Farmer.quality_tier == quality_tier)
    if q:
        term = f"%{q.strip()}%"
        statement = statement.where(
            or_(
                Farmer.first_name.ilike(term),
                Farmer.last_name.ilike(term),
                Farmer.farmer_code.ilike(term),
            )
        )
    count = await session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = await session.scalars(statement.order_by(Farmer.first_name).limit(limit).offset(offset))
    return FarmerPage(count=count or 0, results=[FarmerRow.model_validate(row) for row in rows])


@router.get("/{state_id}/{farmer_id}", response_model=FarmerDetail, name="farmer_detail")
async def get_farmer(
    state_id: int, farmer_id: uuid.UUID, session: SessionDep, caller: CurrentUser
) -> FarmerDetail:
    statement = _territory(
        select(Farmer).where(Farmer.id == farmer_id, Farmer.state_id == state_id), caller
    )
    farmer = await session.scalar(statement)
    if farmer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such farmer in your territory.")
    return FarmerDetail.model_validate(farmer)


@router.post("/import-csv/", response_model=FarmerImportResult, name="farmer_import_csv")
async def import_farmers_csv(
    session: SessionDep,
    caller: CurrentUser,
    source_code: str = Form(...),
    file: UploadFile = File(...),
) -> FarmerImportResult:
    if caller.user.role not in IMPORT_ROLES:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only data operations or compliance may import farmers."
        )
    source = await session.scalar(select(Source).where(Source.code == source_code))
    if source is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown source_code.")
    if not source.is_approved or not source.legal_basis.strip():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "The source is not approved with a legal basis."
        )
    if not source.contains_pii or source.kind not in PII_IMPORT_KINDS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Farmer data requires an approved PII source from a lawful partner, field, signup, Theta or licensed route.",
        )

    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "CSV exceeds 10 MiB.")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    except UnicodeDecodeError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "CSV must be UTF-8.") from error
    required = {"state_id", "first_name"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "CSV requires state_id and first_name columns."
        )

    report = FarmerImportResult()
    now = datetime.now(UTC)
    valid_states = set((await session.scalars(select(State.id))).all())
    for line, row in enumerate(reader, start=2):
        try:
            state_id = int((row.get("state_id") or "").strip())
            first_name = (row.get("first_name") or "").strip()
            if state_id not in valid_states or not first_name:
                raise ValueError("invalid state_id or empty first_name")
            district_id = int(row["district_id"]) if row.get("district_id", "").strip() else None
            external_id = (row.get("theta_external_id") or "").strip() or None
            if external_id and await session.scalar(
                select(Farmer.id).where(
                    Farmer.state_id == state_id, Farmer.theta_external_id == external_id
                )
            ):
                report.skipped += 1
                continue
            area = Decimal(row["total_area_ha"]) if row.get("total_area_ha", "").strip() else None
            farmer = Farmer(
                state_id=state_id,
                first_name=first_name,
                last_name=(row.get("last_name") or "").strip() or None,
                name_local=(row.get("name_local") or "").strip() or None,
                district_id=district_id,
                village_id=int(row["village_id"]) if row.get("village_id", "").strip() else None,
                pincode=(row.get("pincode") or "").strip() or None,
                total_area_ha=area,
                primary_fpo_id=uuid.UUID(row["primary_fpo_id"])
                if row.get("primary_fpo_id", "").strip()
                else None,
                theta_external_id=external_id,
                primary_source_id=source.id,
                owner_user_id=caller.user.public_id,
                created_at=now,
                updated_at=now,
                created_by=caller.user.public_id,
                updated_by=caller.user.public_id,
            )
            session.add(farmer)
            await session.flush()
            for field_name in (
                "first_name",
                "last_name",
                "district_id",
                "village_id",
                "total_area_ha",
                "theta_external_id",
            ):
                value = getattr(farmer, field_name)
                if value is not None:
                    session.add(
                        FieldProvenance(
                            entity_type="farmer",
                            entity_id=farmer.id,
                            field_name=field_name,
                            value_text=str(value),
                            source_id=source.id,
                            source_reference=f"{file.filename or 'upload.csv'}:{line}",
                            confidence=Decimal("0.80"),
                            collected_at=now,
                            is_current=True,
                        )
                    )
            report.created += 1
        except (ValueError, InvalidOperation) as error:
            report.skipped += 1
            if len(report.errors) < 100:
                report.errors.append(f"line {line}: {error}")
    return report
