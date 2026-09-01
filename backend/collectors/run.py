from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select, update

from backend.collectors.sfac import CollectorRefused, Record, SfacFpoCollector
from backend.db import SessionLocal
from backend.models.business import (
    Contradiction,
    District,
    FieldProvenance,
    Organisation,
    Source,
    State,
)

CONFIDENCE = Decimal("0.60")
MARGIN = Decimal("0.15")
WRITABLE = {
    "name",
    "cin",
    "type",
    "legal_form",
    "registration_date",
    "address_line1",
    "state_id",
    "district_id",
}


@dataclass
class Report:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    contradicted: int = 0
    skipped: int = 0


async def permitted_source(session) -> Source:
    source = await session.scalar(select(Source).where(Source.code == "sfac_fpo_list"))
    if source is None or not source.is_approved or not source.legal_basis.strip():
        raise CollectorRefused("R1: sfac_fpo_list is not approved with a written legal basis.")
    if source.contains_pii:
        raise CollectorRefused("R4: this website collector cannot collect personal data.")
    return source


async def upsert(session, source: Source, record: Record, report: Report) -> None:
    cin = record.fields.get("cin")
    if not isinstance(cin, str) or not cin:
        report.skipped += 1
        return
    incoming = {"name": record.name, "cin": cin}
    for key, value in record.fields.items():
        if key in WRITABLE and value not in (None, ""):
            incoming[key] = value
    state_name = record.fields.get("state_name")
    state = (
        await session.scalar(select(State).where(State.name.ilike(str(state_name))))
        if state_name
        else None
    )
    if state:
        incoming["state_id"] = state.id
        district_name = record.fields.get("district_name")
        district = (
            await session.scalar(
                select(District).where(
                    District.state_id == state.id, District.name.ilike(str(district_name))
                )
            )
            if district_name
            else None
        )
        if district:
            incoming["district_id"] = district.id
    if isinstance(incoming.get("registration_date"), str):
        incoming["registration_date"] = date.fromisoformat(incoming["registration_date"])
    now = datetime.now(UTC)
    organisation = await session.scalar(select(Organisation).where(Organisation.cin == cin))
    if organisation is None:
        organisation = Organisation(**incoming, created_at=now, updated_at=now)
        session.add(organisation)
        await session.flush()
        changed = incoming
        report.created += 1
    else:
        changed = {}
        for field_name, value in incoming.items():
            current = getattr(organisation, field_name)
            if str(current or "") == str(value or ""):
                continue
            stored = await session.scalar(
                select(FieldProvenance)
                .where(
                    FieldProvenance.entity_type == "organisation",
                    FieldProvenance.entity_id == organisation.id,
                    FieldProvenance.field_name == field_name,
                    FieldProvenance.is_current.is_(True),
                )
                .order_by(FieldProvenance.collected_at.desc())
            )
            if stored and CONFIDENCE <= stored.confidence + MARGIN:
                session.add(
                    Contradiction(
                        entity_type="organisation",
                        entity_id=organisation.id,
                        field_name=field_name,
                        value_a=str(current or ""),
                        value_b=str(value or ""),
                        provenance_a=stored.id,
                        detected_at=now,
                    )
                )
                report.contradicted += 1
                continue
            changed[field_name] = value
        if not changed:
            report.unchanged += 1
            return
        for field_name, value in changed.items():
            setattr(organisation, field_name, value)
        organisation.updated_at = now
        report.updated += 1
    for field_name, value in changed.items():
        await session.execute(
            update(FieldProvenance)
            .where(
                FieldProvenance.entity_type == "organisation",
                FieldProvenance.entity_id == organisation.id,
                FieldProvenance.field_name == field_name,
                FieldProvenance.is_current.is_(True),
            )
            .values(is_current=False)
        )
        session.add(
            FieldProvenance(
                entity_type="organisation",
                entity_id=organisation.id,
                field_name=field_name,
                value_text=str(value),
                source_id=source.id,
                source_reference=record.reference,
                confidence=CONFIDENCE,
                collected_at=now,
                is_current=True,
            )
        )


async def main_async(args) -> int:
    async with SessionLocal() as session:
        source = await permitted_source(session)
        records = await asyncio.to_thread(SfacFpoCollector(args.states, args.limit).collect)
        print(
            f"Collected {len(records)} institutional FPO records; no farmer/CEO personal data was collected."
        )
        if args.dry_run:
            for record in records[:10]:
                print(record.name, record.fields.get("cin"))
            return 0
        report = Report()
        for record in records:
            await upsert(session, source, record, report)
        await session.commit()
        print(report)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an approved FastAPI-era collector")
    parser.add_argument("collector", choices=["sfac"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--states", nargs="+")
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except CollectorRefused as error:
        print(error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
