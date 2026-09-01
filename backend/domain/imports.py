"""
Bulk import — the batch lifecycle and 🔴 R5 (Doc 06 §3.3).

    upload → land raw → map columns → dry run → confirm legal basis → commit

**R5 is the whole point of this module.** An import cannot commit unless
`legal_basis_confirmed` is true, set by a named user who also referenced the
consent artefact. Everything else here is plumbing around that one refusal.

Three design decisions worth stating, because each closes a way the control
could be walked around:

1. **The gate is checked inside `commit_batch`, not on the screen.** A client
   that skips the confirmation call still cannot commit. The same argument
   INVOICE.md makes for pre-issue checks: a control that depends on a UI
   remembering to ask is not a control.

2. **Confirming the legal basis is a separate call from committing**, and it
   records who and when. One call that both confirmed and committed would make
   the confirmation a parameter of the commit — something a script sets to
   `true` because the endpoint requires it, rather than something a person
   decides.

3. **The dry run and the commit run the same pipeline.** A dry run that
   exercised a different code path would be a preview of something other than
   what happens. It differs only in that its transaction is rolled back.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.deps import Caller
from backend.domain import normalise as norm
from backend.domain import pii
from backend.models.business import Source

# ---------------------------------------------------------------------------
# Confidence — Doc 06 §1 stage 6
# ---------------------------------------------------------------------------

#: Baseline confidence by source kind. 🔴 The gap between `field_collection`
#: (0.95) and everything else is what stops a bulk import erasing an agent's
#: work: the upsert rule requires incoming > existing + 0.15, and no source
#: kind below clears 0.95 by that margin. That is the arithmetic behind
#: CLAUDE.md's "never let a bulk import overwrite a human-verified value".
SOURCE_CONFIDENCE: dict[str, Decimal] = {
    "field_collection": Decimal("0.95"),
    "partner_agreement": Decimal("0.90"),
    "public_registry": Decimal("0.85"),
    "inbound_signup": Decimal("0.85"),
    "open_government_data": Decimal("0.80"),
    "official_website": Decimal("0.75"),
    "industry_directory": Decimal("0.70"),
    "manual_entry": Decimal("0.70"),
    "theta_analytics": Decimal("0.60"),
    "purchased_licensed": Decimal("0.50"),
    "inferred": Decimal("0.40"),
    "unknown": Decimal("0.30"),
}

#: 🔴 Doc 06 stage 5. Incoming must beat existing by more than this to replace
#: it; otherwise the existing value stands and a contradiction is recorded.
CONFIDENCE_MARGIN = Decimal("0.15")


def confidence_for(source_kind: str) -> Decimal:
    return SOURCE_CONFIDENCE.get(source_kind, SOURCE_CONFIDENCE["unknown"])


# ---------------------------------------------------------------------------
# Batch states
# ---------------------------------------------------------------------------

#: The DDL types `status` as text with a comment listing these. Named here so
#: a typo is a Python error rather than a row nothing will ever match.
UPLOADED = "uploaded"
VALIDATING = "validating"
DRY_RUN = "dry_run"
COMMITTED = "committed"
FAILED = "failed"
ROLLED_BACK = "rolled_back"

#: A batch may only be committed from one of these. Committing twice is the
#: mistake this prevents — a retried request that already succeeded.
COMMITTABLE_FROM = frozenset({DRY_RUN, UPLOADED})

#: Doc 06 §3.3 step 6: reversible for 7 days via the batch's provenance rows.
REVERSIBLE_DAYS = 7


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RowError:
    """One row that did not survive validation. Never aborts the batch."""

    row_number: int
    raw: dict[str, Any]
    code: str
    message: str


@dataclass(slots=True)
class PipelineResult:
    """What a run did, or would do."""

    rows_total: int = 0
    rows_created: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0
    contradictions: int = 0
    errors: list[RowError] = field(default_factory=list)
    #: Doc 06 §3.3 step 3 — twenty rows as they *would* be written.
    sample: list[dict[str, Any]] = field(default_factory=list)

    @property
    def rows_error(self) -> int:
        return len(self.errors)

    def as_counts(self) -> dict[str, int]:
        return {
            "rows_total": self.rows_total,
            "rows_created": self.rows_created,
            "rows_updated": self.rows_updated,
            "rows_skipped": self.rows_skipped,
            "rows_error": self.rows_error,
            "contradictions": self.contradictions,
        }


# ---------------------------------------------------------------------------
# 🔴 R5 — the gate
# ---------------------------------------------------------------------------


class LegalBasisNotConfirmed(HTTPException):
    """
    🔴 R5, as an exception rather than a boolean nobody reads.

    Deliberately its own class so a `grep` for it finds every place the rule
    is enforced, and so a future refactor cannot quietly turn the check into a
    warning by changing a status code.
    """

    def __init__(self, batch_id: uuid.UUID) -> None:
        super().__init__(
            status.HTTP_409_CONFLICT,
            f"Import {batch_id} cannot be committed: its lawful basis has not "
            f"been confirmed (R5). A named user must record the basis and the "
            f"consent artefact reference via "
            f"POST /api/v1/imports/{batch_id}/legal-basis/ first.",
        )


async def confirm_legal_basis(
    session: AsyncSession,
    *,
    batch_id: uuid.UUID,
    caller: Caller,
    basis: str,
    consent_evidence_ref: str,
) -> dict[str, Any]:
    """
    🔴 R5. A named user takes responsibility for the lawful basis.

    Both a stated basis and a reference to the artefact are required, and the
    reference is not validated for existence — deliberately. A URL to a signed
    MoU in a document store, an internal ticket, a physical file number: the
    system cannot tell which of these is real, and pretending to check would
    be worse than recording exactly what the person typed and who typed it.

    What it *does* enforce is that both are present and substantive, because a
    single character in each box is the shape this control fails in.
    """
    if len(basis.strip()) < 20:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "State the lawful basis in a sentence — which agreement, with whom, "
            "covering what. This text is what a regulator reads first.",
        )
    if len(consent_evidence_ref.strip()) < 3:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Reference the consent artefact: a document link, an MoU number, "
            "or a file reference. 'None' is an answer — but it means this "
            "import should not proceed.",
        )

    batch = await _batch_row(session, batch_id)
    if batch["status"] == COMMITTED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That import has already been committed; confirming its basis now "
            "would record an approval that did not precede the write.",
        )

    await session.execute(
        text(
            """
            UPDATE dq.import_batch
               SET legal_basis_confirmed = true,
                   consent_evidence_ref  = :ref,
                   mapping = jsonb_set(
                       mapping, '{legal_basis}',
                       to_jsonb(CAST(:basis AS text)), true)
             WHERE id = :id
            """
        ),
        {"id": str(batch_id), "ref": consent_evidence_ref.strip(), "basis": basis.strip()},
    )

    # 🔴 The confirmation is itself an audited act. Who confirmed a lawful
    # basis is the first question asked after an incident, and a boolean
    # column alone cannot answer it.
    await pii.log_access(
        session,
        caller=caller,
        action="confirm_legal_basis",
        entity_type="dq.import_batch",
        record_count=1,
        filters={"batch_id": str(batch_id), "consent_evidence_ref": consent_evidence_ref},
        reason=basis.strip(),
    )
    return await _batch_row(session, batch_id)


async def require_legal_basis(session: AsyncSession, batch_id: uuid.UUID) -> dict[str, Any]:
    """Load a batch and refuse unless R5 is satisfied."""
    batch = await _batch_row(session, batch_id)
    if not batch["legal_basis_confirmed"]:
        raise LegalBasisNotConfirmed(batch_id)
    return batch


# ---------------------------------------------------------------------------
# Batch rows
# ---------------------------------------------------------------------------


async def _batch_row(session: AsyncSession, batch_id: uuid.UUID) -> dict[str, Any]:
    row = await session.execute(
        text(
            """
            SELECT b.id, b.file_name, b.storage_key, b.entity_type, b.source_id,
                   b.mapping, b.status, b.rows_total, b.rows_created,
                   b.rows_updated, b.rows_skipped, b.rows_error,
                   b.legal_basis_confirmed, b.consent_evidence_ref,
                   b.started_at, b.finished_at, b.created_by, b.created_at,
                   s.kind AS source_kind, s.code AS source_code,
                   s.is_approved AS source_approved
              FROM dq.import_batch b
              JOIN dq.source s ON s.id = b.source_id
             WHERE b.id = :id
            """
        ),
        {"id": str(batch_id)},
    )
    record = row.mappings().first()
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such import batch.")
    return dict(record)


async def create_batch(
    session: AsyncSession,
    *,
    caller: Caller,
    file_name: str,
    entity_type: str,
    source_id: int,
    storage_key: str | None,
    mapping: dict[str, str],
    rows_total: int,
) -> dict[str, Any]:
    """
    Stage 1 — LAND. Record the batch against the source it came from.

    🔴 The source is checked here rather than at commit. An operator who has
    uploaded a file, mapped thirty columns and run a dry run should not
    discover at the last step that the source was never approved — and more
    importantly, the raw file should not be landed against a source that
    cannot lawfully supply it.
    """
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"No source with id {source_id}.")
    if not source.is_approved:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Source {source.code!r} is not approved (R1). An unapproved source "
            f"cannot supply an import.",
        )
    if entity_type in PII_ENTITY_TYPES and source.kind not in pii.PII_SOURCE_KINDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Source {source.code!r} is a {source.kind!r} source, which may "
            f"carry institutional facts but not personal data (R4). "
            f"An import of {entity_type!r} records needs one of: "
            f"{', '.join(sorted(pii.PII_SOURCE_KINDS))}.",
        )

    batch_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO dq.import_batch
                (id, file_name, storage_key, entity_type, source_id, mapping,
                 status, rows_total, created_by)
            VALUES
                (:id, :file_name, :storage_key, :entity_type, :source_id,
                 CAST(:mapping AS jsonb), :status, :rows_total, :created_by)
            """
        ),
        {
            "id": str(batch_id),
            "file_name": file_name,
            "storage_key": storage_key,
            "entity_type": entity_type,
            "source_id": source_id,
            "mapping": json.dumps(mapping),
            "status": UPLOADED,
            "rows_total": rows_total,
            "created_by": str(caller.user.public_id),
        },
    )
    return await _batch_row(session, batch_id)


#: Entity types whose rows are personal data, and so subject to R4 at upload.
#: `organisation` is absent: an FPO name and CIN are institutional facts.
PII_ENTITY_TYPES = frozenset({"person", "farmer", "contact_point"})


async def record_errors(
    session: AsyncSession, *, batch_id: uuid.UUID, errors: list[RowError]
) -> None:
    """Write `dq.import_row_error`. A bad row is a report line, not an abort."""
    for row_error in errors:
        await session.execute(
            text(
                """
                INSERT INTO dq.import_row_error
                    (batch_id, row_number, raw, error_code, error_message)
                VALUES (:batch, :row, CAST(:raw AS jsonb), :code, :message)
                """
            ),
            {
                "batch": str(batch_id),
                "row": row_error.row_number,
                "raw": json.dumps(row_error.raw, default=str),
                "code": row_error.code,
                "message": row_error.message,
            },
        )


async def finish_batch(
    session: AsyncSession,
    *,
    batch_id: uuid.UUID,
    status_value: str,
    result: PipelineResult,
) -> dict[str, Any]:
    await session.execute(
        text(
            """
            UPDATE dq.import_batch
               SET status = :status,
                   rows_total = :rows_total,
                   rows_created = :rows_created,
                   rows_updated = :rows_updated,
                   rows_skipped = :rows_skipped,
                   rows_error = :rows_error,
                   finished_at = now(),
                   started_at = coalesce(started_at, now())
             WHERE id = :id
            """
        ),
        {
            "id": str(batch_id),
            "status": status_value,
            **result.as_counts(),
        },
    )
    return await _batch_row(session, batch_id)


# ---------------------------------------------------------------------------
# Stage 2–3 — NORMALISE and VALIDATE
# ---------------------------------------------------------------------------

#: What each entity type must carry after mapping. Doc 06 stage 3, structural.
REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "organisation": frozenset({"name"}),
    "person": frozenset({"first_name"}),
    "farmer": frozenset({"first_name", "state"}),
}


def normalise_row(
    entity_type: str, row: dict[str, Any], *, row_number: int
) -> tuple[dict[str, Any], RowError | None]:
    """
    Stages 2 and 3 for one mapped row.

    Returns the cleaned row, or `None` plus the error that stopped it. Never
    raises: one unparseable row must not take the batch down with it.
    """
    cleaned: dict[str, Any] = {}

    required = REQUIRED_FIELDS.get(entity_type, frozenset())
    missing = sorted(f for f in required if not str(row.get(f, "")).strip())
    if missing:
        return {}, RowError(
            row_number,
            row,
            "missing_required",
            f"Required field(s) absent or blank: {', '.join(missing)}.",
        )

    for key, value in row.items():
        if value is None or str(value).strip() == "":
            continue
        try:
            cleaned[key] = _normalise_field(key, value, row)
        except norm.NormaliseError as error:
            return {}, RowError(row_number, row, error.code, f"{key}: {error}")

    return cleaned, None


def _normalise_field(key: str, value: Any, row: dict[str, Any]) -> Any:
    """
    Dispatch on the mapped field name.

    🔴 `area` is the one that needs a sibling column: a bigha cannot be
    converted without the state, so the whole row is passed in rather than the
    single value. That coupling is the point — a signature that could not see
    the state would have to guess.
    """
    if key in {"total_area_ha", "area"}:
        unit = str(row.get("area_unit", "ha"))
        return norm.area_to_hectares(value, unit, state=row.get("state"))
    if key in {"first_name", "middle_name", "last_name", "name", "father_or_spouse"}:
        return norm.normalise_name(str(value))
    if key in {"mobile", "phone", "whatsapp"}:
        return norm.normalise_contact("mobile", str(value))
    if key == "email":
        return norm.normalise_contact("email", str(value))
    if key == "cin":
        return norm.normalise_cin(str(value))
    if key.endswith("_date") or key in {"date_of_birth", "registration_date"}:
        return norm.parse_date(str(value))
    if key.endswith("_inr") or key in {"turnover", "share_capital"}:
        return norm.parse_money(value)
    if key.startswith(("is_", "has_")):
        return norm.parse_bool(value)
    return str(value).strip()


# ---------------------------------------------------------------------------
# Stage 5 — UPSERT, the rule that protects an agent's work
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FieldDecision:
    """What the merge rule decided for one field, and why."""

    field_name: str
    action: str  # take_incoming | keep_existing | refresh | contradiction
    existing: Any = None
    incoming: Any = None
    reason: str = ""


def merge_field(
    *,
    field_name: str,
    existing: Any,
    incoming: Any,
    existing_confidence: Decimal,
    incoming_confidence: Decimal,
) -> FieldDecision:
    """
    Doc 06 stage 5, exactly as written there.

    🔴 The `+ 0.15` margin is the load-bearing part. Without it a 0.86 registry
    value replaces a 0.85 one on a coin flip, and the register churns; with it,
    replacing a field-verified value (0.95) requires a source that does not
    exist. A tie does not overwrite — it refreshes the timestamp, because two
    sources agreeing is evidence the value is right, not a reason to rewrite it.
    """
    if incoming is None or str(incoming).strip() == "":
        return FieldDecision(field_name, "keep_existing", existing, incoming, "incoming is empty")
    if existing is None or str(existing).strip() == "":
        return FieldDecision(field_name, "take_incoming", existing, incoming, "existing is empty")
    if str(existing).strip() == str(incoming).strip():
        return FieldDecision(field_name, "refresh", existing, incoming, "values agree")
    if incoming_confidence > existing_confidence + CONFIDENCE_MARGIN:
        return FieldDecision(
            field_name,
            "take_incoming",
            existing,
            incoming,
            f"incoming {incoming_confidence} beats existing {existing_confidence} "
            f"by more than {CONFIDENCE_MARGIN}",
        )
    return FieldDecision(
        field_name,
        "contradiction",
        existing,
        incoming,
        f"incoming {incoming_confidence} does not beat existing "
        f"{existing_confidence} by {CONFIDENCE_MARGIN}; existing value stands",
    )


async def record_contradiction(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    decision: FieldDecision,
) -> None:
    """A disagreement is kept, not resolved by the importer."""
    await session.execute(
        text(
            """
            INSERT INTO dq.contradiction
                (entity_type, entity_id, field_name, value_a, value_b, detected_at)
            VALUES (:entity_type, :entity_id, :field_name, :a, :b, now())
            """
        ),
        {
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "field_name": decision.field_name,
            "a": str(decision.existing),
            "b": str(decision.incoming),
        },
    )


# ---------------------------------------------------------------------------
# Stage 6 — PROVENANCE
# ---------------------------------------------------------------------------


async def record_provenance(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    field_name: str,
    value: Any,
    source_id: int,
    confidence: Decimal,
    batch_id: uuid.UUID | None = None,
) -> None:
    """
    Write `dq.field_provenance` and retire the previous row.

    🔴 The old row is marked `is_current = false`, never deleted. That is what
    makes an import reversible for seven days (Doc 06 §3.3 step 6) — the
    previous value and its source are still there to restore.
    """
    await session.execute(
        text(
            """
            UPDATE dq.field_provenance
               SET is_current = false
             WHERE entity_type = :entity_type
               AND entity_id = :entity_id
               AND field_name = :field_name
               AND is_current
            """
        ),
        {"entity_type": entity_type, "entity_id": str(entity_id), "field_name": field_name},
    )
    await session.execute(
        text(
            """
            INSERT INTO dq.field_provenance
                (entity_type, entity_id, field_name, value_text, source_id,
                 source_reference, confidence, collected_at, is_current)
            VALUES
                (:entity_type, :entity_id, :field_name, :value, :source_id,
                 :source_reference, :confidence, now(), true)
            """
        ),
        {
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "field_name": field_name,
            "value": str(value),
            "source_id": source_id,
            "source_reference": f"import_batch:{batch_id}" if batch_id else None,
            "confidence": confidence,
        },
    )


async def existing_confidence(
    session: AsyncSession, *, entity_type: str, entity_id: uuid.UUID, field_name: str
) -> Decimal | None:
    """The confidence currently standing behind a field, if anything does."""
    value = await session.scalar(
        text(
            """
            SELECT confidence FROM dq.field_provenance
             WHERE entity_type = :entity_type AND entity_id = :entity_id
               AND field_name = :field_name AND is_current
             ORDER BY collected_at DESC LIMIT 1
            """
        ).bindparams(entity_type=entity_type, entity_id=entity_id, field_name=field_name)
    )
    return Decimal(str(value)) if value is not None else None


# ---------------------------------------------------------------------------
# Reversal
# ---------------------------------------------------------------------------


async def batch_is_reversible(session: AsyncSession, batch_id: uuid.UUID) -> tuple[bool, str]:
    """
    Whether a committed batch is still inside its seven-day window.

    Returns the reason too, so the endpoint can say *why* rather than 409 with
    nothing an operator can act on.
    """
    batch = await _batch_row(session, batch_id)
    if batch["status"] != COMMITTED:
        return False, f"The batch is {batch['status']}, not committed."
    finished = batch["finished_at"]
    if finished is None:
        return False, "The batch has no completion time recorded."
    age_days = (datetime.now(UTC) - finished).days
    if age_days > REVERSIBLE_DAYS:
        return False, (
            f"The batch was committed {age_days} days ago; reversal is offered "
            f"for {REVERSIBLE_DAYS} days, after which later edits may depend on it."
        )
    return True, ""


__all__ = [
    "COMMITTABLE_FROM",
    "COMMITTED",
    "CONFIDENCE_MARGIN",
    "DRY_RUN",
    "FAILED",
    "PII_ENTITY_TYPES",
    "REQUIRED_FIELDS",
    "REVERSIBLE_DAYS",
    "ROLLED_BACK",
    "SOURCE_CONFIDENCE",
    "UPLOADED",
    "VALIDATING",
    "FieldDecision",
    "LegalBasisNotConfirmed",
    "PipelineResult",
    "RowError",
    "batch_is_reversible",
    "confidence_for",
    "confirm_legal_basis",
    "create_batch",
    "existing_confidence",
    "finish_batch",
    "merge_field",
    "normalise_row",
    "record_contradiction",
    "record_errors",
    "record_provenance",
    "require_legal_basis",
]
