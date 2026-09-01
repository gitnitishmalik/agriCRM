"""
Extraction hardening — validation, duplicate detection and cross-checks.

The extraction agent itself (`api/agent.py`) already does the hard part well:
text-layer first, vision only for photos and scans, arithmetic recomputed
rather than trusted. This module is what wraps it.

🔴 **Three things it adds, each from a named failure:**

1. **The file is validated by content, not by name.** A `.pdf` name and an
   `application/pdf` header cost nothing to forge, and the parser that opens
   the file next is the thing being protected.

2. **The same document uploaded twice is caught.** By file hash first — the
   cheapest and most certain signal — then by the tuple that identifies an
   invoice: seller/buyer GSTIN, number, date and total. Uploading the same
   invoice again is the commonest way one gets billed twice.

3. **What the model proposed is stored beside what the human accepted.** That
   pairing is the evaluation set. Golden cases that come from real corrections
   are worth more than ones invented by guessing at what is hard, and the
   11B-vision case in INVOICE.md — a complete fictional invoice, confidently
   produced — is exactly what a guess would have missed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import gstin as gstin_lib
from backend.domain.scoping import EntityScope
from backend.models.billing import Invoice
from backend.models.business import Organisation
from backend.models.invoice_ops import ContractRate, InvoiceExtraction
from backend.money import compute_line, sum_lines

#: A PDF with more pages than this is not an invoice — it is a contract, a
#: scanned folder or an attack. Reading it costs time and memory before
#: anything notices.
MAX_PAGES = 20

#: Pixels, width × height. A 50-megapixel phone photo decompresses to hundreds
#: of megabytes; the cap is checked before the decode, which is the point.
MAX_PIXELS = 50_000_000


@dataclass
class Finding:
    """One cross-check result, in the same shape the pre-issue checks use."""

    code: str
    severity: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------


def page_count(content: bytes, content_type: str) -> int | None:
    """
    How many pages a PDF has, without rendering any of them.

    Returns None for anything that is not a PDF, or when the file cannot be
    parsed — a page count is a guard, and a guard that raises on a malformed
    file has turned into a second attack surface.
    """
    if content_type != "application/pdf":
        return None
    try:
        import io

        import pypdf

        return len(pypdf.PdfReader(io.BytesIO(content)).pages)
    except Exception:  # noqa: BLE001 — a malformed PDF must not raise here
        return None


def validate_document(content: bytes, *, declared_name: str = "") -> tuple[str, int | None]:
    """
    Validate a document and return its real type and page count.

    🔴 Order matters: size, then content sniff, then page count. Each step is
    cheaper than the next, and the expensive one never runs on a file the cheap
    one would have rejected.
    """
    from backend.domain.storage import StorageError, validate_upload

    content_type = validate_upload(content, declared_name=declared_name)
    pages = page_count(content, content_type)

    if pages is not None and pages > MAX_PAGES:
        raise StorageError(
            f"That PDF has {pages} pages; the limit is {MAX_PAGES}. An invoice is "
            f"one or two — a longer document is usually a contract or a whole "
            f"folder scanned in one go. Split it and upload the invoice."
        )

    if content_type.startswith("image/"):
        pixels = _image_pixels(content)
        if pixels and pixels > MAX_PIXELS:
            raise StorageError(
                f"That image is {pixels / 1_000_000:.0f} megapixels, which is more "
                f"than this can decode safely. A photo of an invoice at 8 MP is "
                f"more than enough to read."
            )

    return content_type, pages


def _image_pixels(content: bytes) -> int | None:
    """
    Dimensions from the header, without decoding the image.

    🔴 The whole point is not decoding it. A crafted image whose header claims
    modest dimensions is a different problem; one whose header is honest about
    being 50 megapixels is caught here for the cost of reading a few bytes.
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            return width * height
    except Exception:  # noqa: BLE001 — Pillow is optional and may not be present
        return None


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


async def find_duplicates(
    session: AsyncSession,
    scope: EntityScope,
    *,
    sha256: bytes,
    extracted: dict[str, Any],
    exclude_extraction_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID | None, list[str], list[Finding]]:
    """
    Has this document been seen before?

    Returns (matched invoice id, reasons, findings). Two signals, in order of
    certainty:

    1. **The same bytes.** Certain, cheap, and the commonest case — somebody
       uploads the file again because they are not sure the first one worked.
    2. **The same invoice identity.** Seller GSTIN, buyer GSTIN, number, date
       and total. A re-scan of the same document has different bytes and the
       same identity, and that is the case worth catching.
    """
    reasons: list[str] = []
    findings: list[Finding] = []
    matched: uuid.UUID | None = None

    # 1 — identical bytes.
    #
    # 🔴 `exclude_extraction_id` is not optional in practice. The attempt is
    # recorded *before* the model runs, so by the time this executes the
    # current upload is already a row with this exact hash — and without the
    # exclusion every first upload reports itself as a duplicate of itself.
    # Storing first is right (a failure that leaves no trace teaches nobody);
    # the query has to account for it.
    conditions_hash = [InvoiceExtraction.sha256 == sha256]
    if exclude_extraction_id is not None:
        conditions_hash.append(InvoiceExtraction.id != exclude_extraction_id)

    prior = await session.scalar(
        select(InvoiceExtraction)
        .where(and_(*conditions_hash))
        .order_by(InvoiceExtraction.created_at.desc())
        .limit(1)
    )
    if prior is not None:
        reasons.append("file_hash")
        matched = prior.invoice_id
        findings.append(
            Finding(
                code="duplicate_file",
                severity="warning",
                message=(
                    f"This exact file was uploaded before, on "
                    f"{prior.created_at.date().isoformat()}"
                    + (
                        " and accepted onto an invoice."
                        if prior.invoice_id
                        else ", though it was not accepted onto an invoice."
                    )
                ),
                evidence={
                    "prior_extraction_id": str(prior.id),
                    "prior_invoice_id": str(prior.invoice_id) if prior.invoice_id else None,
                    "uploaded_at": prior.created_at.isoformat(),
                },
            )
        )

    # 2 — the same invoice identity, different bytes.
    invoice_no = (extracted.get("invoice_no") or "").strip()
    buyer_gstin = (extracted.get("buyer_gstin") or "").strip().upper()
    total = _decimal(extracted.get("total_value"))
    invoice_date = _date(extracted.get("invoice_date"))

    if invoice_no or (buyer_gstin and total):
        conditions = [Invoice.is_deleted.is_(False)]
        identity: list[Any] = []

        if invoice_no:
            identity.append(func.lower(Invoice.invoice_no) == invoice_no.lower())
        if buyer_gstin and total is not None:
            same_document = [
                func.upper(func.coalesce(Invoice.buyer_gstin, "")) == buyer_gstin,
                Invoice.total_value == total,
            ]
            if invoice_date:
                same_document.append(
                    Invoice.invoice_date.between(
                        invoice_date - timedelta(days=3), invoice_date + timedelta(days=3)
                    )
                )
            identity.append(and_(*same_document))

        if identity:
            conditions.append(or_(*identity))
            candidates = list(
                await session.scalars(select(Invoice).where(and_(*conditions)).limit(5))
            )
            if candidates:
                if matched is None:
                    matched = candidates[0].id
                reasons.append("invoice_identity")
                findings.append(
                    Finding(
                        code="duplicate_invoice",
                        severity="warning",
                        message=(
                            "An invoice with the same identity already exists: "
                            + ", ".join(
                                f"{c.invoice_no or 'draft'} dated "
                                f"{c.invoice_date.isoformat()} for {c.total_value}"
                                for c in candidates
                            )
                            + ". Confirm this is a different document before accepting it."
                        ),
                        evidence={
                            "candidates": [
                                {
                                    "id": str(c.id),
                                    "invoice_no": c.invoice_no,
                                    "invoice_date": c.invoice_date.isoformat(),
                                    "total_value": str(c.total_value),
                                }
                                for c in candidates
                            ]
                        },
                    )
                )

    return matched, reasons, findings


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Cross-checks
# ---------------------------------------------------------------------------


async def cross_check(
    session: AsyncSession,
    scope: EntityScope,
    *,
    extracted: dict[str, Any],
    organisation_id: uuid.UUID | None = None,
) -> list[Finding]:
    """
    Everything checkable about what the model read, before a human accepts it.

    🔴 Arithmetic first, because it is the only check that can be certain. A
    stated total that disagrees with the lines is either a misread rate or a
    misread total, and either way the document should not be accepted on the
    model's word.
    """
    findings: list[Finding] = []

    # -- Arithmetic ---------------------------------------------------------
    lines = extracted.get("lines") or []
    computed = []
    for index, line in enumerate(lines, 1):
        quantity = _decimal(line.get("quantity"))
        rate = _decimal(line.get("rate"))
        if quantity is None or rate is None:
            findings.append(
                Finding(
                    code="line_incomplete",
                    severity="warning",
                    message=(
                        f"Line {index} is missing "
                        + ("a quantity" if quantity is None else "a rate")
                        + ". It cannot be checked and must be typed in."
                    ),
                    evidence={"line": index},
                )
            )
            continue

        amounts = compute_line(
            qty=quantity,
            rate=rate,
            tax_rate_pct=_decimal(extracted.get("tax_rate_pct")) or Decimal("18.00"),
            rate_is_tax_inclusive=bool(line.get("rate_is_tax_inclusive")),
            taxable_supply=extracted.get("tax_treatment", "igst") in ("igst", "cgst_sgst"),
        )
        computed.append(amounts)

        stated = _decimal(line.get("line_total"))
        if stated is not None and stated != amounts.total:
            findings.append(
                Finding(
                    code="line_total_mismatch",
                    severity="error",
                    message=(
                        f"Line {index}: {quantity} × {rate} computes to "
                        f"{amounts.total}, but the document states {stated}. "
                        f"One of the three numbers was misread."
                    ),
                    evidence={
                        "line": index,
                        "computed": str(amounts.total),
                        "stated": str(stated),
                    },
                )
            )

    if computed:
        header = sum_lines(computed)
        stated_total = _decimal(extracted.get("total_value"))
        if stated_total is not None and stated_total != header.total:
            findings.append(
                Finding(
                    code="total_mismatch",
                    severity="error",
                    message=(
                        f"The lines sum to {header.total}; the document states "
                        f"{stated_total}. 🔴 Never accept the stated figure over the "
                        f"computed one — a rate read as 2301 instead of 150 gives an "
                        f"identical product and no other check can catch it."
                    ),
                    evidence={"computed": str(header.total), "stated": str(stated_total)},
                )
            )

    # -- GSTIN --------------------------------------------------------------
    buyer_gstin = (extracted.get("buyer_gstin") or "").strip()
    if buyer_gstin:
        try:
            normalised = gstin_lib.validate(buyer_gstin, allow_govt_uin=True)
            if normalised != buyer_gstin.upper():
                findings.append(
                    Finding(
                        code="gstin_normalised",
                        severity="info",
                        message=f"Read '{buyer_gstin}' as '{normalised}'.",
                        evidence={"as_read": buyer_gstin, "normalised": normalised},
                    )
                )
        except gstin_lib.GSTINError as error:
            findings.append(
                Finding(
                    code="gstin_invalid",
                    severity="error",
                    message=(
                        f"The GSTIN read off the document does not validate: {error} "
                        f"Check it against the document before accepting."
                    ),
                    evidence={"as_read": buyer_gstin},
                )
            )

    # -- The selected organisation -----------------------------------------
    if organisation_id is not None:
        organisation = await session.scalar(
            select(Organisation).where(Organisation.id == organisation_id)
        )
        if organisation is not None:
            org_gstin = (organisation.gstin or "").strip().upper()
            if org_gstin and buyer_gstin and org_gstin != buyer_gstin.strip().upper():
                findings.append(
                    Finding(
                        code="organisation_gstin_mismatch",
                        severity="error",
                        message=(
                            f"The document's GSTIN ({buyer_gstin}) is not the one on "
                            f"{organisation.name}'s record ({org_gstin}). Either the "
                            f"wrong customer is selected, or this is a different state's "
                            f"registration."
                        ),
                        evidence={"document": buyer_gstin, "registry": org_gstin},
                    )
                )

    # -- The contract rate --------------------------------------------------
    invoice_date = _date(extracted.get("invoice_date")) or datetime.now(UTC).date()
    if organisation_id is not None and lines:
        contracts = list(
            await session.scalars(
                select(ContractRate).where(
                    ContractRate.billing_entity_id.in_(scope.entity_ids),
                    ContractRate.organisation_id == organisation_id,
                    ContractRate.valid_from <= invoice_date,
                    or_(
                        ContractRate.valid_to.is_(None),
                        ContractRate.valid_to >= invoice_date,
                    ),
                )
            )
        )
        for index, line in enumerate(lines, 1):
            rate = _decimal(line.get("rate"))
            unit = line.get("unit")
            if rate is None or not unit:
                continue
            match = next((c for c in contracts if c.unit == unit), None)
            if match is None or match.rate == 0:
                continue
            variance = (rate - match.rate) / match.rate * 100
            if abs(variance) > (match.tolerance_pct or Decimal(0)):
                findings.append(
                    Finding(
                        code="rate_variance",
                        severity="warning",
                        message=(
                            f"Line {index} reads a rate of {rate} against a contracted "
                            f"{match.rate} ({variance:+.1f}%)."
                        ),
                        evidence={
                            "line": index,
                            "read": str(rate),
                            "contract": str(match.rate),
                        },
                    )
                )

    # -- Inclusive-tax consistency -----------------------------------------
    flags = {bool(line.get("rate_is_tax_inclusive")) for line in lines}
    if len(flags) > 1:
        findings.append(
            Finding(
                code="mixed_tax_inclusive",
                severity="warning",
                message=(
                    "Some lines were read as tax-inclusive and some as not. That is "
                    "legitimate but unusual on one document — confirm both, because "
                    "getting one wrong misstates revenue by the tax fraction."
                ),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


async def record_extraction(
    session: AsyncSession,
    scope: EntityScope,
    *,
    file_name: str,
    content_type: str | None,
    size_bytes: int,
    sha256: bytes | None,
    pages: int | None,
    source_object_id: uuid.UUID | None = None,
    billing_entity_id: uuid.UUID | None = None,
) -> InvoiceExtraction:
    """
    Open a record before the model runs.

    🔴 Before, not after. A failure that leaves no trace is a failure nobody
    learns from, and the interesting extractions are exactly the ones that
    went wrong.
    """
    row = InvoiceExtraction(
        file_name=file_name,
        mime_type=content_type,
        size_bytes=size_bytes,
        sha256=sha256,
        page_count=pages,
        source_object_id=source_object_id,
        billing_entity_id=billing_entity_id,
        status="pending",
        created_at=datetime.now(UTC),
        created_by=scope.user_id,
    )
    session.add(row)
    await session.flush()
    return row


async def record_acceptance(
    session: AsyncSession,
    scope: EntityScope,
    extraction: InvoiceExtraction,
    *,
    invoice_id: uuid.UUID,
    accepted_values: dict[str, Any],
) -> InvoiceExtraction:
    """
    Store what the human actually accepted, beside what the model proposed.

    🔴 This pairing *is* the evaluation set. A golden case built from a real
    correction is worth more than one invented by guessing at what is hard —
    and the failure INVOICE.md records, an 11B vision model confidently
    producing a complete fictional invoice, is precisely what a guess would
    have missed.
    """
    extraction.invoice_id = invoice_id
    extraction.accepted_values = accepted_values
    extraction.accepted_at = datetime.now(UTC)
    extraction.accepted_by = scope.user_id
    await session.flush()
    return extraction


def corrections(extraction: InvoiceExtraction) -> list[dict[str, Any]]:
    """
    Field by field, what the model said against what was accepted.

    The rows where they differ are the ones worth turning into evaluation
    cases; the rows where they agree are the ones that already work.
    """
    proposed = extraction.extracted or {}
    accepted = extraction.accepted_values or {}
    if not accepted:
        return []

    rows = []
    for key in sorted(set(proposed) | set(accepted)):
        if key == "lines":
            continue
        was, now = proposed.get(key), accepted.get(key)
        if str(was or "") != str(now or ""):
            rows.append({"field": key, "model_read": was, "human_accepted": now})
    return rows
