"""
Invoice extraction — upload a photo or PDF, get the create form back filled in.

🔴 Returns a draft payload, never an invoice. Nothing is written to
`crm.invoice` and no number is allocated: a human confirms what the model read
before it becomes a document. An agent that could issue an invoice on its own
would be a model with signing authority.

🔴 **The warnings come before the values.** The response puts `findings` and
`duplicate` ahead of `draft` because a review workbench that shows the
extracted figures first and the contradictions underneath is a workbench where
somebody clicks accept before scrolling.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select

from backend.agent import ExtractionError, extract, to_draft_payload
from backend.deps import SessionDep
from backend.domain import extraction as service
from backend.domain import storage as object_storage
from backend.domain.hashing import sha256_bytes
from backend.domain.scoping import BILLING_WRITE, Scope
from backend.models.billing import BillingEntity
from backend.models.invoice_ops import InvoiceExtraction

router = APIRouter(prefix="/api/v1/invoices", tags=["billing"])


@router.post("/extract/", name="invoice_extract")
async def extract_document(
    session: SessionDep,
    scope: Scope,
    file: UploadFile = File(..., description="A PDF or a photo of the invoice."),
    entity_code: str = Form("TEPL"),
    organisation: uuid.UUID | None = Form(None),
) -> dict:
    """
    Read an uploaded document and return a draft the create form can load.

    Every attempt is recorded in `crm.invoice_extraction`, including the
    failures — a model that misread a document is something to look at later,
    and a failure that leaves no trace is a failure nobody learns from.
    """
    scope.require(BILLING_WRITE, "upload a document for extraction")

    content = await file.read()

    # 🔴 Validate by content before anything opens it. Size, then the real
    # type read from the leading bytes, then the page count — each step is
    # cheaper than the next, and the expensive one never runs on a file the
    # cheap one would have rejected.
    try:
        content_type, pages = service.validate_document(content, declared_name=file.filename or "")
    except object_storage.StorageError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    digest = sha256_bytes(content)

    entity = await session.scalar(
        select(BillingEntity).where(
            BillingEntity.code == entity_code.upper(), BillingEntity.valid_to.is_(None)
        )
    )

    stored = await object_storage.store(
        session,
        content,
        content_type=content_type,
        purpose="upload",
        original_name=file.filename,
        billing_entity_id=entity.id if entity else None,
        created_by=scope.user_id,
    )

    record = await service.record_extraction(
        session,
        scope,
        file_name=file.filename or "upload",
        content_type=content_type,
        size_bytes=len(content),
        sha256=digest,
        pages=pages,
        source_object_id=stored.object_id,
        billing_entity_id=entity.id if entity else None,
    )

    try:
        result = extract(content, file_name=file.filename or "upload", mime_type=content_type)
    except ExtractionError as error:
        record.status = "failed"
        record.error = str(error)
        await session.flush()
        # 🔴 400, and the message is about their file rather than about our
        # environment. "Pillow is not installed" is true, unhelpful, and a
        # different answer from the other provider for the same upload.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    draft = to_draft_payload(result, entity_code=entity_code)

    duplicate_id, duplicate_reasons, duplicate_findings = await service.find_duplicates(
        session,
        scope,
        sha256=digest,
        extracted=draft,
        # 🔴 This upload's own row already exists — it is written before the
        # model runs so a failure leaves a trace. Without excluding it, every
        # first upload matches itself.
        exclude_extraction_id=record.id,
    )
    findings = duplicate_findings + await service.cross_check(
        session, scope, extracted=draft, organisation_id=organisation
    )

    record.status = "succeeded"
    record.error = None
    record.extracted = draft
    record.field_confidence = result.confidence
    record.warnings = list(result.warnings)
    record.extraction_path = getattr(result, "path", None)
    record.model = getattr(result, "model", None)
    record.duplicate_of_invoice_id = duplicate_id
    record.duplicate_reasons = duplicate_reasons
    await session.flush()

    blocking = [f for f in findings if f.severity == "error"]

    return {
        "extraction_id": str(record.id),
        # 🔴 Warnings first. A workbench that renders the figures and the
        # contradictions underneath is one where somebody accepts before
        # scrolling.
        "findings": [f.as_dict() for f in findings],
        "blocking_count": len(blocking),
        "can_accept_directly": not blocking,
        "duplicate": {
            "invoice_id": str(duplicate_id) if duplicate_id else None,
            "reasons": duplicate_reasons,
        },
        "warnings": result.warnings,
        "confidence": result.confidence,
        "file": {
            "sha256": digest.hex(),
            "content_type": content_type,
            "pages": pages,
            "size_bytes": len(content),
        },
        "draft": draft,
    }


@router.post("/extract/{extraction_id}/accept/", name="invoice_extract_accept")
async def accept_extraction(
    extraction_id: uuid.UUID,
    invoice: Annotated[uuid.UUID, Form()],
    session: SessionDep,
    scope: Scope,
) -> dict:
    """
    Record that a human accepted this extraction onto a draft.

    🔴 What was accepted is stored beside what the model proposed. That pairing
    is the evaluation set: a golden case built from a real correction is worth
    more than one invented by guessing at what is hard.
    """
    scope.require(BILLING_WRITE, "accept an extraction")

    record = await session.scalar(
        select(InvoiceExtraction).where(InvoiceExtraction.id == extraction_id)
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such extraction.")

    from backend.models.billing import Invoice

    target = await session.scalar(select(Invoice).where(Invoice.id == invoice))
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")
    scope.check(target.billing_entity_id, what="invoice")

    accepted = {
        "invoice_no": target.invoice_no,
        "invoice_date": target.invoice_date.isoformat(),
        "buyer_name": target.buyer_name,
        "buyer_gstin": target.buyer_gstin,
        "total_value": str(target.total_value),
        "tax_treatment": target.tax_treatment,
        "lines": [
            {
                "description": line.description,
                "quantity": str(line.quantity),
                "unit": line.unit,
                "rate": str(line.rate),
            }
            for line in target.lines
        ],
    }

    await service.record_acceptance(
        session, scope, record, invoice_id=target.id, accepted_values=accepted
    )

    return {
        "extraction_id": str(record.id),
        "invoice_id": str(target.id),
        # The rows where the model and the human disagreed. These are the ones
        # worth turning into evaluation cases.
        "corrections": service.corrections(record),
    }
