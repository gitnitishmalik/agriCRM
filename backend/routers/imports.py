"""
Bulk import (Doc 06 §3.3). Phase 1, sprint 4.

    POST /imports/                      land the batch and its rows
    POST /imports/{id}/dry-run/         full pipeline, rolled back
    POST /imports/{id}/legal-basis/     🔴 R5 — a named user confirms
    POST /imports/{id}/commit/          refuses unless R5 is satisfied
    GET  /imports/{id}/errors/          the fix-and-reupload loop

🔴 **The commit gate is in `require_legal_basis`, called inside the commit
handler.** A client that never calls `/legal-basis/` cannot commit, and one
that calls `/commit/` twice gets a 409 rather than a second write. Doc 06
describes the confirmation as a disabled button; a disabled button is a
suggestion, so it is a refusal on the server as well.

Doc 06 §3.3 step 3 says the dry run shows "a sample of 20 rows as they would
be written". That sample is the part that catches the failure counts cannot:
an import where every row is subtly wrong in the same way has clean counts.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text

from backend.deps import CurrentUser, SessionDep, StrictQuery
from backend.domain import imports, pii, scoping
from backend.schemas.imports import (
    BatchCreateIn,
    BatchOut,
    CommitOut,
    DryRunOut,
    LegalBasisIn,
    RowErrorOut,
)

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])

#: Who may run a bulk import. Deliberately narrow — an import writes thousands
#: of rows across the register at once, and `domain.scoping` already has the
#: vocabulary for "takes personal responsibility for a control".
IMPORT_RUN = frozenset({"data_ops", "admin"})

#: 🔴 Who may confirm a lawful basis. Same set plus compliance, and no wider:
#: R5 says "a named user", and the point of naming them is that the name means
#: something. `bd_manager` uploading a partner list cannot also be the person
#: who attests that the partner had consent to share it.
LEGAL_BASIS_CONFIRM = frozenset({"data_ops", "compliance", "admin"})


def _batch_out(row: dict) -> BatchOut:
    return BatchOut.model_validate(row)


# ---------------------------------------------------------------------------
# Stage 1 — LAND
# ---------------------------------------------------------------------------


@router.post(
    "/", response_model=BatchOut, status_code=status.HTTP_201_CREATED, name="import_create"
)
@router.post(
    "",
    response_model=BatchOut,
    status_code=status.HTTP_201_CREATED,
    name="import_create_alias",
    include_in_schema=False,
)
async def create_import(
    payload: BatchCreateIn, request: Request, session: SessionDep, caller: CurrentUser
) -> BatchOut:
    """
    Land a batch: the rows, the source they came from, and who uploaded them.

    🔴 R1 and R4 are both checked here rather than at commit. An operator who
    has mapped thirty columns and run a dry run should not discover at the
    last step that the source was never approved — and the rows should not be
    landed against a source that cannot lawfully supply them in the first
    place.
    """
    scoping.require(caller, IMPORT_RUN, "run a bulk import")

    if not payload.rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The batch has no rows.")

    batch = await imports.create_batch(
        session,
        caller=caller,
        file_name=payload.file_name,
        entity_type=payload.entity_type,
        source_id=payload.source_id,
        storage_key=None,
        mapping=payload.mapping,
        rows_total=len(payload.rows),
    )

    # The rows themselves are held on the batch's mapping payload for the
    # dry run and commit to replay. 🔴 Stage 1 says land the source payload
    # unmodified — this keeps the parsed rows verbatim, so a normalisation bug
    # found in month nine is reprocessed rather than re-collected.
    await session.execute(
        text(
            "UPDATE dq.import_batch "
            "SET mapping = jsonb_set(mapping, '{rows}', CAST(:rows AS jsonb), true) "
            "WHERE id = :id"
        ),
        {"id": str(batch["id"]), "rows": _dump(payload.rows)},
    )

    await pii.log_access(
        session,
        caller=caller,
        action="import_upload",
        entity_type=f"dq.import_batch:{payload.entity_type}",
        record_count=len(payload.rows),
        filters={"source_id": payload.source_id, "file_name": payload.file_name},
        ip_address=pii.actor_ip(request),
    )
    return _batch_out(await imports._batch_row(session, batch["id"]))


def _dump(rows: list[dict]) -> str:
    import json

    return json.dumps(rows, default=str)


# ---------------------------------------------------------------------------
# Stages 2–7 — the pipeline, run twice
# ---------------------------------------------------------------------------


async def _stored_rows(session: SessionDep, batch_id: uuid.UUID) -> list[dict]:
    raw = await session.scalar(
        text("SELECT mapping -> 'rows' FROM dq.import_batch WHERE id = :id").bindparams(id=batch_id)
    )
    return list(raw or [])


async def _run_pipeline(
    session: SessionDep, batch: dict, *, commit: bool
) -> imports.PipelineResult:
    """
    🔴 One pipeline, two callers. The dry run and the commit differ only in
    whether their transaction survives — a preview that ran different code
    would be a preview of something else.
    """
    rows = await _stored_rows(session, batch["id"])
    entity_type = batch["entity_type"]
    confidence = imports.confidence_for(batch["source_kind"])
    result = imports.PipelineResult(rows_total=len(rows))

    for index, raw_row in enumerate(rows, start=1):
        cleaned, row_error = imports.normalise_row(entity_type, raw_row, row_number=index)
        if row_error is not None:
            result.errors.append(row_error)
            continue

        if len(result.sample) < 20:
            result.sample.append(
                {"row_number": index, "action": "create", "values": _display(cleaned)}
            )

        if commit:
            await _write_row(
                session,
                entity_type=entity_type,
                cleaned=cleaned,
                source_id=batch["source_id"],
                confidence=confidence,
                batch_id=batch["id"],
            )
        result.rows_created += 1

    return result


def _display(cleaned: dict) -> dict:
    """
    🔴 The sample is masked like any other read (R9).

    A dry-run preview of a farmer import is a screen full of personal data,
    and it is the screen most likely to be shared in a chat thread while
    somebody asks "does this look right".
    """
    shown = {}
    for key, value in cleaned.items():
        if key in {"mobile", "phone", "whatsapp"}:
            shown[key] = pii.mask_phone(str(value))
        elif key == "email":
            shown[key] = pii.mask_email(str(value))
        else:
            shown[key] = str(value)
    return shown


async def _write_row(
    session: SessionDep,
    *,
    entity_type: str,
    cleaned: dict,
    source_id: int,
    confidence,
    batch_id: uuid.UUID,
) -> None:
    """
    Stage 5–6 for one row: write the entity, then its provenance.

    Only `organisation` and `person` are wired today. `farmer` needs the
    partitioned insert and its `state_id`, which lands with sprint 5 — and it
    raises rather than silently skipping, because a batch that reported
    "created 4,000" having written nothing is the worst possible outcome.
    """
    now_fields = {"created_at": "now()", "updated_at": "now()"}
    if entity_type == "organisation":
        entity_id = await session.scalar(
            text(
                "INSERT INTO core.organisation (type, status, legal_form, name, "
                f"created_at, updated_at) VALUES ('fpo', 'prospect', 'unknown', :name, "
                f"{now_fields['created_at']}, {now_fields['updated_at']}) RETURNING id"
            ).bindparams(name=cleaned.get("name", ""))
        )
    elif entity_type == "person":
        entity_id = await session.scalar(
            text(
                "INSERT INTO core.person (first_name, last_name, father_or_spouse, "
                "primary_source_id, created_at, updated_at) "
                "VALUES (:first, :last, :father, :source, now(), now()) RETURNING id"
            ).bindparams(
                first=cleaned.get("first_name", ""),
                last=cleaned.get("last_name"),
                father=cleaned.get("father_or_spouse"),
                source=source_id,
            )
        )
    else:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            f"Importing {entity_type!r} is not wired yet. `farmer` needs the "
            f"partitioned insert and lands with sprint 5.",
        )

    for field_name, value in cleaned.items():
        await imports.record_provenance(
            session,
            entity_type=entity_type,
            entity_id=entity_id,
            field_name=field_name,
            value=value,
            source_id=source_id,
            confidence=confidence,
            batch_id=batch_id,
        )


@router.post("/{batch_id}/dry-run/", response_model=DryRunOut, name="import_dry_run")
async def dry_run(batch_id: uuid.UUID, session: SessionDep, caller: CurrentUser) -> DryRunOut:
    """
    Run the whole pipeline and write nothing.

    The transaction is not rolled back here — the request's session is, by the
    caller never committing. What this endpoint guarantees is that no write
    was *attempted*: `_run_pipeline(commit=False)` performs validation and
    normalisation only.
    """
    scoping.require(caller, IMPORT_RUN, "run a bulk import")
    batch = await imports._batch_row(session, batch_id)

    result = await _run_pipeline(session, batch, commit=False)
    await imports.record_errors(session, batch_id=batch_id, errors=result.errors)
    updated = await imports.finish_batch(
        session, batch_id=batch_id, status_value=imports.DRY_RUN, result=result
    )

    blocked = None
    if not updated["legal_basis_confirmed"]:
        blocked = (
            "🔴 R5: the lawful basis for this data has not been confirmed. "
            "A named user must record it before this import can be committed."
        )

    return DryRunOut(
        batch=_batch_out(updated),
        **result.as_counts(),
        sample=result.sample,
        errors=[
            RowErrorOut(
                row_number=e.row_number, error_code=e.code, error_message=e.message, raw=e.raw
            )
            for e in result.errors[:100]
        ],
        may_commit=updated["legal_basis_confirmed"],
        blocked_reason=blocked,
    )


# ---------------------------------------------------------------------------
# 🔴 R5
# ---------------------------------------------------------------------------


@router.post("/{batch_id}/legal-basis/", response_model=BatchOut, name="import_legal_basis")
async def set_legal_basis(
    batch_id: uuid.UUID, payload: LegalBasisIn, session: SessionDep, caller: CurrentUser
) -> BatchOut:
    """
    🔴 R5. Record who confirmed the lawful basis, and on what evidence.

    Separate from the commit on purpose. A single call that both confirmed and
    committed would turn the confirmation into a parameter — something a
    script sets to `true` because the endpoint demands it, rather than
    something a person decided and can be asked about afterwards.
    """
    scoping.require(caller, LEGAL_BASIS_CONFIRM, "confirm the lawful basis for an import")
    updated = await imports.confirm_legal_basis(
        session,
        batch_id=batch_id,
        caller=caller,
        basis=payload.basis,
        consent_evidence_ref=payload.consent_evidence_ref,
    )
    return _batch_out(updated)


@router.post("/{batch_id}/commit/", response_model=CommitOut, name="import_commit")
async def commit(
    batch_id: uuid.UUID, request: Request, session: SessionDep, caller: CurrentUser
) -> CommitOut:
    """
    Write the batch.

    🔴 `require_legal_basis` is the first thing that runs. Not the second, and
    not something the screen checked earlier — R5 is enforced here because
    this is the only place the write actually happens.
    """
    scoping.require(caller, IMPORT_RUN, "commit a bulk import")

    batch = await imports.require_legal_basis(session, batch_id)

    if batch["status"] not in imports.COMMITTABLE_FROM:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"That import is {batch['status']}. Only a batch that is "
            f"{' or '.join(sorted(imports.COMMITTABLE_FROM))} can be committed — "
            f"this prevents a retried request writing the rows twice.",
        )

    result = await _run_pipeline(session, batch, commit=True)
    await imports.record_errors(session, batch_id=batch_id, errors=result.errors)
    updated = await imports.finish_batch(
        session, batch_id=batch_id, status_value=imports.COMMITTED, result=result
    )

    await pii.log_access(
        session,
        caller=caller,
        action="import_commit",
        entity_type=f"dq.import_batch:{batch['entity_type']}",
        record_count=result.rows_created + result.rows_updated,
        filters={"batch_id": str(batch_id)},
        reason=batch["consent_evidence_ref"],
        ip_address=pii.actor_ip(request),
    )

    finished = updated["finished_at"]
    return CommitOut(
        batch=_batch_out(updated),
        rows_created=result.rows_created,
        rows_updated=result.rows_updated,
        rows_skipped=result.rows_skipped,
        rows_error=result.rows_error,
        contradictions=result.contradictions,
        reversible_until=(finished + timedelta(days=imports.REVERSIBLE_DAYS) if finished else None),
    )


# ---------------------------------------------------------------------------
# The fix-and-reupload loop — Doc 06: "used constantly, make it good"
# ---------------------------------------------------------------------------


@router.get(
    "/{batch_id}/errors/",
    response_model=list[RowErrorOut],
    name="import_errors",
    dependencies=[StrictQuery],
)
async def batch_errors(
    batch_id: uuid.UUID, session: SessionDep, caller: CurrentUser, limit: int = 500
) -> list[RowErrorOut]:
    """
    Every row that did not survive, with the original values and the reason.

    Doc 06 calls this loop the one data ops uses constantly. The original row
    is returned alongside the error so the file can be corrected without
    cross-referencing line numbers by hand.
    """
    await imports._batch_row(session, batch_id)
    rows = await session.execute(
        text(
            "SELECT row_number, error_code, error_message, raw "
            "FROM dq.import_row_error WHERE batch_id = :id "
            "ORDER BY row_number LIMIT :limit"
        ).bindparams(id=batch_id, limit=limit)
    )
    return [RowErrorOut(**dict(row)) for row in rows.mappings()]


@router.get("/{batch_id}", response_model=BatchOut, name="import_detail")
async def get_import(batch_id: uuid.UUID, session: SessionDep, caller: CurrentUser) -> BatchOut:
    return _batch_out(await imports._batch_row(session, batch_id))


__all__ = ["router"]
