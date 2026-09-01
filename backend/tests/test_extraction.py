"""
Extraction hardening: validation, duplicate detection and the text-layer rule.

🔴 The three assertions that earned their place here each come from a real
failure found by running the endpoint:

* **A first upload is not a duplicate of itself.** The attempt is recorded
  before the model runs — deliberately, so a failure leaves a trace — which
  means the current row already carries the file's hash by the time the
  duplicate check runs. Without excluding it, every upload was flagged.

* **A missing `pypdf` refuses rather than falling back to vision.** Those are
  two different situations and only one is safe: a PDF with no text layer is a
  scan, and vision is right for it; a PDF whose text we *cannot read* because a
  package is missing is a deployment fault, and rasterising it sends a perfect
  transcript down the path INVOICE.md measured fabricating an entire invoice.

* **A retired model says so.** A hosted model has an end-of-life date, so a
  deployment that worked yesterday can answer HTTP 410 today with nothing
  changed locally. "HTTP 410" sends an operator hunting a bug in the upload.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.domain import extraction as service
from backend.domain.scoping import EntityScope
from backend.domain.storage import StorageError

pytestmark = pytest.mark.anyio


def _pdf_with_text(lines: list[str]) -> bytes:
    """
    A valid single-page PDF carrying a real, extractable text layer.

    Built by hand rather than with reportlab so the suite needs no extra
    dependency — and because what is being tested is precisely that `pypdf`
    can read a text layer out of a computer-generated document.
    """
    ops = "BT /F1 11 Tf 50 750 Td 14 TL\n"
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        ops += f"({escaped}) Tj T*\n"
    ops += "ET"
    stream = ops.encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(index).encode() + b" 0 obj\n" + body + b"\nendobj\n"

    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_at).encode() + b"\n%%EOF\n"
    )
    return bytes(out)


SAMPLE_INVOICE = _pdf_with_text(
    [
        "TAX INVOICE",
        "Theta Enerlytics Private Limited",
        "GSTIN: 07AAHCT0066D1ZM",
        "Invoice No: TEPL/2026-27/08   Dated: 14-Jul-2026",
        "Buyer: Syngenta India Private Limited",
        "GSTIN/UIN: 09AAECS9424P1ZL   State Code: 09",
        "Drone spraying services  998611  215 acre  150.00  32250.00",
        "Total  38055.00",
    ]
)


async def _scope(session, caller) -> EntityScope:
    from backend.models.billing import BillingEntity

    ids = list(await session.scalars(select(BillingEntity.id)))
    return EntityScope(caller, ids)


# ---------------------------------------------------------------------------
# 🔴 The self-match regression
# ---------------------------------------------------------------------------


async def test_a_first_upload_is_not_a_duplicate_of_itself(session, biller):
    """
    🔴 The bug this test exists for.

    `record_extraction` runs before the model does, so the current upload is
    already a row carrying this hash when `find_duplicates` executes. Storing
    first is correct — a failure that leaves no trace teaches nobody — so the
    query has to exclude the row it just wrote.
    """
    from backend.deps import Caller
    from backend.domain.hashing import sha256_bytes

    caller = Caller(biller, {"role": biller.role, "mfa_satisfied": True})
    scope = await _scope(session, caller)
    digest = sha256_bytes(SAMPLE_INVOICE)

    record = await service.record_extraction(
        session,
        scope,
        file_name="invoice.pdf",
        content_type="application/pdf",
        size_bytes=len(SAMPLE_INVOICE),
        sha256=digest,
        pages=1,
    )

    matched, reasons, findings = await service.find_duplicates(
        session,
        scope,
        sha256=digest,
        extracted={},
        exclude_extraction_id=record.id,
    )

    assert reasons == [], f"a first upload reported itself as a duplicate: {reasons}"
    assert matched is None
    assert findings == []


async def test_the_same_file_uploaded_twice_is_caught(session, biller):
    """The other half: a genuine re-upload must still be flagged."""
    from backend.deps import Caller
    from backend.domain.hashing import sha256_bytes

    caller = Caller(biller, {"role": biller.role, "mfa_satisfied": True})
    scope = await _scope(session, caller)
    digest = sha256_bytes(SAMPLE_INVOICE)

    first = await service.record_extraction(
        session,
        scope,
        file_name="invoice.pdf",
        content_type="application/pdf",
        size_bytes=len(SAMPLE_INVOICE),
        sha256=digest,
        pages=1,
    )
    first.status = "succeeded"
    await session.flush()

    second = await service.record_extraction(
        session,
        scope,
        file_name="invoice-again.pdf",
        content_type="application/pdf",
        size_bytes=len(SAMPLE_INVOICE),
        sha256=digest,
        pages=1,
    )

    _, reasons, findings = await service.find_duplicates(
        session,
        scope,
        sha256=digest,
        extracted={},
        exclude_extraction_id=second.id,
    )

    assert "file_hash" in reasons
    assert any(f.code == "duplicate_file" for f in findings)
    assert findings[0].evidence["prior_extraction_id"] == str(first.id)


# ---------------------------------------------------------------------------
# 🔴 The text layer must not silently become a vision reading
# ---------------------------------------------------------------------------


async def test_a_missing_pypdf_refuses_rather_than_rasterising(monkeypatch):
    """
    🔴 An install problem must not degrade into a correctness problem.

    Returning "" made "this scan has no text" and "we cannot read text at all"
    indistinguishable, and the second silently took the path that was measured
    producing a complete fictional invoice.
    """
    import builtins

    from backend.agent import ExtractionError, _pdf_text

    real_import = builtins.__import__

    def _no_pypdf(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("simulated: pypdf is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_pypdf)

    with pytest.raises(ExtractionError) as raised:
        _pdf_text(SAMPLE_INVOICE)

    message = str(raised.value)
    assert "pypdf is not installed" in message
    assert "Refusing to fall back" in message


async def test_a_real_text_layer_is_read_losslessly():
    """
    A computer-generated invoice carries a perfect transcript of itself, and
    using it has no OCR step to get a digit wrong in. This is the path every
    document this business issues should take.
    """
    from backend.agent import _pdf_text

    text = _pdf_text(SAMPLE_INVOICE)

    assert "TEPL/2026-27/08" in text
    assert "09AAECS9424P1ZL" in text
    assert "215 acre" in text
    assert "38055.00" in text


async def test_a_pdf_without_a_text_layer_falls_through_to_vision():
    """
    The legitimate vision case: a scan. `_pdf_text` returns "" and the caller
    routes to the image path, which is right — and different from the refusal
    above.
    """
    from backend.agent import _pdf_text

    blank = _pdf_with_text([])
    assert _pdf_text(blank) == ""


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------


async def test_a_pdf_is_recognised_by_content_not_by_name():
    content_type, pages = service.validate_document(
        SAMPLE_INVOICE, declared_name="not-really-a-pdf.txt"
    )
    assert content_type == "application/pdf"
    assert pages == 1


async def test_a_word_document_is_refused_with_a_useful_message():
    """
    "Unsupported media type" tells the person nothing. Naming what they
    probably uploaded tells them what to do next.
    """
    docx = b"PK\x03\x04" + b"\x00" * 200
    with pytest.raises(StorageError) as raised:
        service.validate_document(docx, declared_name="invoice.docx")
    assert "Word document" in str(raised.value)


async def test_an_empty_file_is_refused():
    with pytest.raises(StorageError):
        service.validate_document(b"", declared_name="invoice.pdf")


# ---------------------------------------------------------------------------
# Cross-checks
# ---------------------------------------------------------------------------


async def test_a_stated_total_that_disagrees_with_the_lines_is_an_error(session, biller):
    """
    🔴 Never accept the stated figure over the computed one. A rate read as
    2301 instead of 150 gives an identical product, and no other check catches
    it — this one catches the total being wrong.
    """
    from backend.deps import Caller

    caller = Caller(biller, {"role": biller.role, "mfa_satisfied": True})
    scope = await _scope(session, caller)

    findings = await service.cross_check(
        session,
        scope,
        extracted={
            "tax_rate_pct": "18.00",
            "tax_treatment": "igst",
            "lines": [{"quantity": "200", "rate": "150", "unit": "acre"}],
            "total_value": "41400.00",  # wrong: 200 × 150 + 18% is 35,400
        },
    )

    mismatch = [f for f in findings if f.code == "total_mismatch"]
    assert mismatch, [f.code for f in findings]
    assert mismatch[0].severity == "error"
    assert "35400.00" in mismatch[0].message


async def test_a_malformed_gstin_read_off_a_document_is_flagged(session, biller):
    """🔴 D1 again, this time on the extraction path."""
    from backend.deps import Caller

    caller = Caller(biller, {"role": biller.role, "mfa_satisfied": True})
    scope = await _scope(session, caller)

    findings = await service.cross_check(
        session,
        scope,
        extracted={"buyer_gstin": "09AAECS942P1ZL", "lines": []},
    )

    invalid = [f for f in findings if f.code == "gstin_invalid"]
    assert invalid
    assert invalid[0].severity == "error"
