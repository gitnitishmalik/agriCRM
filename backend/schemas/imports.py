"""Bulk import shapes (Doc 06 §3.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BatchCreateIn(BaseModel):
    """
    Stage 1 — LAND.

    `rows` are the parsed source rows, already mapped to CRM field names by
    `mapping`. Parsing XLSX/CSV happens at the edge; this endpoint takes the
    rows so the pipeline has one input shape whether they came from a file, a
    partner API or a paste.
    """

    file_name: str
    entity_type: str
    source_id: int
    mapping: dict[str, str] = Field(default_factory=dict)
    rows: list[dict[str, Any]]


class LegalBasisIn(BaseModel):
    """
    🔴 R5. Both fields are required and both are checked for substance.

    `basis` is prose because that is what a regulator reads: which agreement,
    with whom, covering what. A dropdown would produce twelve identical
    answers and tell nobody anything.
    """

    basis: str
    consent_evidence_ref: str


class RowErrorOut(BaseModel):
    row_number: int
    error_code: str
    error_message: str
    raw: dict[str, Any]


class BatchOut(BaseModel):
    id: uuid.UUID
    file_name: str
    entity_type: str
    source_id: int
    source_code: str | None = None
    source_kind: str | None = None
    status: str
    rows_total: int
    rows_created: int
    rows_updated: int
    rows_skipped: int
    rows_error: int
    #: 🔴 R5. False means the batch cannot be committed, whatever else is true.
    legal_basis_confirmed: bool
    consent_evidence_ref: str | None = None
    storage_key: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_by: uuid.UUID
    created_at: datetime


class DryRunOut(BaseModel):
    """
    What a commit would do, without doing it.

    `sample` is twenty rows as they *would* be written — Doc 06 §3.3 step 3.
    Counts alone let somebody approve an import whose every row is subtly
    wrong in the same way.
    """

    batch: BatchOut
    rows_total: int
    rows_created: int
    rows_updated: int
    rows_skipped: int
    rows_error: int
    contradictions: int
    sample: list[dict[str, Any]]
    errors: list[RowErrorOut]
    #: 🔴 Restates the gate on every dry run, so a client never has to infer it.
    may_commit: bool
    blocked_reason: str | None = None


class CommitOut(BaseModel):
    batch: BatchOut
    rows_created: int
    rows_updated: int
    rows_skipped: int
    rows_error: int
    contradictions: int
    reversible_until: datetime | None = None
