"""
The extraction agent: an uploaded invoice photo or PDF becomes a filled form.

Claude reads the document and returns structured fields. That is the whole
feature — you photograph a work order or a supplier's invoice, and the create
form arrives populated instead of blank.

Three design decisions worth stating, because each is the difference between a
useful tool and a liability:

1. **It fills a draft. It never issues.** An extraction is a suggestion with a
   confidence attached, exactly like any other machine-derived value in this
   system (`dq.field_provenance`). A number that a model read off a photograph
   is not the same kind of fact as a number a person typed, and the invoice
   register must not pretend otherwise. Nothing here allocates an invoice
   number.

2. **Arithmetic is recomputed, never trusted.** The model is asked for
   quantities and rates, not for totals. Every amount on the generated invoice
   comes from `money.compute_line`. If the model also reports a total and ours
   disagrees, that becomes a warning for a human — which is how you catch both
   a misread digit and a mistake in the original document.

3. **It reads, it does not decide.** Tax treatment is captured, never inferred
   (INVOICE.md §5.4 is still open with the CA). A GSTIN that fails validation
   is reported as a warning rather than silently corrected, because "correcting"
   a customer's tax number is how you file a return against the wrong party.


Moved from `apps/billing/agent.py` during the FastAPI migration. The only
change is where settings come from — every provider call, every guard and the
file-type validation are unchanged. 🔴 That validation is the one worth not
touching: the type is checked before any provider is dispatched to, so an
unsupported upload gets the same sentence whichever backend is configured
rather than "Pillow is not installed".
"""

from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from backend import gstin as gstin_lib
from backend.config import settings
from backend.money import compute_line

logger = logging.getLogger(__name__)

#: Anthropic's document block accepts PDF directly; images go as image blocks.
SUPPORTED_IMAGE = {"image/jpeg", "image/png", "image/gif", "image/webp"}
SUPPORTED_PDF = {"application/pdf"}

#: 🔴 The API caps a base64 request at ~32MB and a PDF at 100 pages. A phone
#: photo is well inside that; a scanned year of invoices is not. Refusing early
#: with a clear message beats a truncated read that silently loses lines.
MAX_BYTES = 20 * 1024 * 1024

DEFAULT_MODEL = "claude-opus-5"


class ExtractionError(RuntimeError):
    """Raised with a message intended for the person who uploaded the file."""


# The schema Claude fills. Described as a tool rather than asked for in prose,
# because a tool schema is enforced by the API and a prose request is not — the
# difference between "usually returns JSON" and "returns this shape".
EXTRACTION_TOOL: dict[str, Any] = {
    "name": "record_invoice",
    "description": (
        "Record the details read from an invoice, work order or proforma. "
        "Report only what is visibly present in the document. Use null for "
        "anything absent — never infer, complete or correct a value."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "document_kind": {
                "type": "string",
                "enum": [
                    "tax_invoice",
                    "proforma_invoice",
                    "work_order",
                    "purchase_order",
                    "other",
                ],
            },
            "invoice_no": {"type": ["string", "null"]},
            "invoice_date": {"type": ["string", "null"], "description": "ISO 8601, YYYY-MM-DD"},
            "supplier_name": {"type": ["string", "null"], "description": "Who issued it"},
            "supplier_gstin": {"type": ["string", "null"]},
            "buyer_name": {"type": ["string", "null"], "description": "Bill To party"},
            "buyer_address": {"type": ["string", "null"]},
            "buyer_gstin": {"type": ["string", "null"], "description": "Exactly as printed"},
            "buyer_pan": {"type": ["string", "null"]},
            "consignee_name": {"type": ["string", "null"], "description": "Ship To, if separate"},
            "consignee_address": {"type": ["string", "null"]},
            "buyer_order_no": {
                "type": ["string", "null"],
                "description": "PO or Buyer's Order No.",
            },
            "work_order_ref": {"type": ["string", "null"]},
            "letter_ref": {"type": ["string", "null"]},
            "data_link_url": {"type": ["string", "null"]},
            "tax_rate_pct": {"type": ["number", "null"], "description": "18 for IGST 18%"},
            "tax_kind": {
                "type": ["string", "null"],
                "enum": ["igst", "cgst_sgst", "none", None],
                "description": "What the document shows. 'none' if no tax line is present.",
            },
            "rate_is_tax_inclusive": {
                "type": ["boolean", "null"],
                "description": "True only where the document says the rate includes GST.",
            },
            "stated_taxable_value": {
                "type": ["number", "null"],
                "description": "As printed, for cross-check",
            },
            "stated_tax_amount": {"type": ["number", "null"]},
            "stated_total": {"type": ["number", "null"]},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "hsn_sac": {"type": ["string", "null"]},
                        "quantity": {"type": ["number", "null"]},
                        "unit": {
                            "type": ["string", "null"],
                            "enum": [
                                "acre",
                                "sq_km",
                                "hectare",
                                "each",
                                "lump_sum",
                                "day",
                                "hour",
                                None,
                            ],
                        },
                        "rate": {"type": ["number", "null"]},
                        "stated_amount": {"type": ["number", "null"]},
                        "location_note": {"type": ["string", "null"]},
                    },
                    "required": ["description"],
                },
            },
            "confidence": {
                "type": "object",
                "description": (
                    "0.0-1.0 per field name you filled. Low where the document "
                    "is blurred, handwritten, cropped or ambiguous."
                ),
                "additionalProperties": {"type": "number"},
            },
            "notes": {
                "type": ["string", "null"],
                "description": "Anything a human should look at: illegible figures, an unclear unit.",
            },
        },
        "required": ["document_kind", "lines", "confidence"],
    },
}

SYSTEM_PROMPT = """You read Indian GST invoices, proforma invoices and work orders and record what is on them.

Rules:
- Record only what is visibly printed. Never infer, complete or correct a value.
- A GSTIN is 15 characters. If the document shows fewer, record exactly what is
  printed and say so in notes. Do not repair it.
- Quantities are often in acres (drone spraying) or square kilometres (drone
  survey). Record the unit the document uses. Do not convert.
- If a rate is described as inclusive of GST, set rate_is_tax_inclusive true.
- Record stated totals as printed so they can be cross-checked. Do not
  recompute them; arithmetic is done downstream.
- Give an honest confidence per field. A blurred or cropped figure should score
  low. Overconfidence is worse than a blank field, because a blank field gets
  typed in and a wrong confident one gets sent to a customer.
"""


@dataclass
class ExtractionResult:
    """What the agent read, plus what we make of it."""

    extracted: dict[str, Any]
    confidence: dict[str, float]
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] | None = None
    model: str = DEFAULT_MODEL
    duration_ms: int = 0
    #: 🔴 `text_layer` or `vision`. Which one ran matters more than which model
    #: is configured: a rasterised computer-generated PDF throws away a perfect
    #: transcript and asks a model to reconstruct it, and the reconstruction is
    #: where the errors come from. Recorded on `crm.invoice_extraction` so the
    #: console can be scanned for readings that took the wrong path.
    path: str = "text_layer"

    @property
    def needs_review(self) -> bool:
        """
        True when a human must look before this becomes a document.

        Deliberately generous: a low score anywhere, any warning at all, or a
        missing buyer. The cost of an unnecessary review is a few seconds; the
        cost of a wrong invoice is a customer conversation.
        """
        if self.warnings:
            return True
        if not self.extracted.get("buyer_name"):
            return True
        return any(score < 0.75 for score in self.confidence.values())


def _normalise(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Force a reply into the shape the rest of the module expects.

    🔴 This is the cost of a provider with no enforced tool schema. The model
    is *asked* for `lines` as an array of objects and usually obliges — but a
    real reply during testing came back with `lines` as a bare float, which
    made `_check` raise a TypeError deep in the request instead of telling the
    user anything useful.

    Nothing here invents data. A value that cannot be coerced into the right
    shape is dropped, and the missing-field warnings in `_check` then say so in
    language the uploader can act on.
    """
    out = dict(payload)

    raw_lines = out.get("lines")
    if isinstance(raw_lines, dict):
        # A single line returned unwrapped.
        raw_lines = [raw_lines]
    elif not isinstance(raw_lines, list):
        raw_lines = []

    out["lines"] = [line for line in raw_lines if isinstance(line, dict)]

    confidence = out.get("confidence")
    out["confidence"] = confidence if isinstance(confidence, dict) else {}

    # Strings where we expect strings, so `.strip()` downstream cannot explode.
    for key in ("invoice_no", "buyer_name", "buyer_gstin", "supplier_gstin", "document_kind"):
        value = out.get(key)
        if value is not None and not isinstance(value, str):
            out[key] = str(value)

    return out


def _decimal(value: Any) -> Decimal | None:
    """
    A number out of a model reply, or None.

    Strips Indian digit grouping: a reply may hand back "21,02,400.00" rather
    than 2102400, and a failed parse here silently disables the cross-check
    that is the whole point of reading the stated totals.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("₹", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _check(result: ExtractionResult) -> None:
    """
    Cross-check what the model read against arithmetic and against the GSTIN rules.

    Every finding is a warning, never a correction. The point is to hand a human
    a list of things to look at, not to quietly produce a different invoice from
    the one in the photograph.
    """
    data = result.extracted
    warnings = result.warnings

    # -- GSTIN ------------------------------------------------------------
    for label, key in (("Buyer", "buyer_gstin"), ("Supplier", "supplier_gstin")):
        raw = data.get(key)
        if not raw:
            continue
        try:
            gstin_lib.validate(raw, allow_govt_uin=True)
        except gstin_lib.GSTINError as exc:
            # 🔴 This is the defect that runs through 29 lines of the historical
            # sheet. Surfacing it here is how it stops spreading.
            warnings.append(f"{label} GSTIN '{raw}': {exc}")

    # -- line arithmetic ---------------------------------------------------
    tax_pct = _decimal(data.get("tax_rate_pct")) or Decimal(18)
    inclusive = bool(data.get("rate_is_tax_inclusive"))
    taxable_supply = data.get("tax_kind") not in {"none", None}

    computed_taxable = Decimal(0)
    computed_tax = Decimal(0)

    for index, line in enumerate(data.get("lines") or [], 1):
        qty = _decimal(line.get("quantity"))
        rate = _decimal(line.get("rate"))
        stated = _decimal(line.get("stated_amount"))

        if qty is None or rate is None:
            if stated is not None:
                warnings.append(
                    f"Line {index}: an amount of {stated} is printed but the quantity "
                    f"or rate could not be read. Fill both in before issuing."
                )
            continue

        amounts = compute_line(
            qty=qty,
            rate=rate,
            tax_rate_pct=tax_pct,
            rate_is_tax_inclusive=inclusive,
            taxable_supply=taxable_supply,
        )
        computed_taxable += amounts.taxable
        computed_tax += amounts.tax

        # Compare against whichever figure the document actually prints in its
        # Amount column. On a tax-exclusive line that is the taxable base; on a
        # tax-inclusive one — the Mizoram survey rate — it is the gross, tax
        # already inside it. Comparing the wrong one flags every correct
        # inclusive invoice, which trains people to ignore the warnings.
        printed = amounts.total if inclusive else amounts.taxable

        if stated is not None and abs(printed - stated) > Decimal(1):
            warnings.append(
                f"Line {index}: {qty} x {rate} comes to {printed}, "
                f"but the document shows {stated}. Check the quantity and rate."
            )

    # -- header totals -----------------------------------------------------
    stated_total = _decimal(data.get("stated_total"))
    if stated_total is not None:
        computed_total = computed_taxable + computed_tax
        if computed_total and abs(computed_total - stated_total) > Decimal(1):
            warnings.append(
                f"The lines add up to {computed_total}, but the document's total "
                f"reads {stated_total}. One of the two is wrong."
            )

    # -- things that simply have to be there -------------------------------
    if not data.get("lines"):
        warnings.append("No line items could be read from this document.")
    if not data.get("buyer_name"):
        warnings.append("No Bill To party could be read. Pick the customer by hand.")
    if data.get("document_kind") in {"work_order", "purchase_order"}:
        warnings.append(
            "This looks like a work order rather than an invoice — the amounts may "
            "be a contract value rather than what you are billing now."
        )


def extract(
    content: bytes,
    *,
    file_name: str,
    mime_type: str | None = None,
    model: str | None = None,
    provider: str | None = None,
) -> ExtractionResult:
    """
    Read an invoice document and return structured fields.

    Dispatches to the configured provider. Raises ``ExtractionError`` with a
    message for the uploader when the file cannot be read at all; returns a
    result carrying warnings when it can be read but something about it does
    not add up.
    """
    provider = (provider or settings.invoice_extraction_provider or "anthropic").lower()

    if len(content) > MAX_BYTES:
        raise ExtractionError(
            f"{file_name} is {len(content) // 1_048_576} MB. "
            f"The limit is {MAX_BYTES // 1_048_576} MB — try a single page, or a photo "
            f"rather than a scan."
        )

    mime_type = mime_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    # 🔴 Type check before provider dispatch, so what the uploader gets back is
    # about their file rather than about our environment. The NVIDIA path
    # rasterises through Pillow and used to reach its import first, answering a
    # .docx with "Pillow is not installed" — true, unhelpful, and a different
    # answer from the Anthropic path for the same upload. One check here means
    # every provider refuses the same files with the same sentence.
    if mime_type not in SUPPORTED_PDF | SUPPORTED_IMAGE:
        raise ExtractionError(
            f"{file_name} is a {mime_type} file. Upload a PDF or a photo (JPEG, PNG, WebP)."
        )

    if provider == "nvidia":
        result = _extract_nvidia(
            content,
            file_name=file_name,
            mime_type=mime_type,
            model=model or settings.nvidia_vision_model,
        )
        _check(result)
        return result

    if provider != "anthropic":
        raise ExtractionError(
            f"Unknown extraction provider '{provider}'. "
            f"Set INVOICE_EXTRACTION_PROVIDER to 'anthropic' or 'nvidia'."
        )

    return _extract_anthropic(
        content, file_name=file_name, mime_type=mime_type, model=model or DEFAULT_MODEL
    )


def _extract_anthropic(
    content: bytes, *, file_name: str, mime_type: str, model: str
) -> ExtractionResult:
    """
    Claude. Reads PDFs natively and fills a forced tool schema, which is what
    guarantees a shape back rather than prose that usually parses.
    """
    if not settings.anthropic_api_key:
        raise ExtractionError(
            "Document reading is not configured on this server. "
            "Set ANTHROPIC_API_KEY, or fill the form by hand."
        )

    if mime_type in SUPPORTED_PDF:
        block = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(content).decode(),
            },
        }
    elif mime_type in SUPPORTED_IMAGE:
        block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": base64.b64encode(content).decode(),
            },
        }
    else:
        raise ExtractionError(
            f"{file_name} is a {mime_type} file. Upload a PDF or a photo (JPEG, PNG, WebP)."
        )

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - environment problem
        raise ExtractionError(
            "The anthropic package is not installed on this server. Run: pip install anthropic"
        ) from exc

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    started = time.monotonic()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[EXTRACTION_TOOL],
            # Forcing the tool is what guarantees a shape back. Left to itself
            # the model may reasonably answer in prose, and prose is not a form.
            tool_choice={"type": "tool", "name": "record_invoice"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        block,
                        {
                            "type": "text",
                            "text": (
                                "Record every detail on this document using the "
                                "record_invoice tool. Report what is printed, not "
                                "what you expect an invoice to say."
                            ),
                        },
                    ],
                }
            ],
        )
    except Exception as exc:
        logger.exception("Invoice extraction failed for %s", file_name)
        raise ExtractionError(f"The document could not be read: {exc}") from exc

    duration_ms = int((time.monotonic() - started) * 1000)

    payload = next(
        (b.input for b in response.content if getattr(b, "type", None) == "tool_use"),
        None,
    )
    if payload is None:
        raise ExtractionError("Nothing recognisable as an invoice was found in this document.")

    result = ExtractionResult(
        extracted=_normalise(dict(payload)),
        confidence={k: float(v) for k, v in (payload.get("confidence") or {}).items()},
        raw=json.loads(response.model_dump_json()),
        model=model,
        duration_ms=duration_ms,
        # Claude reads a PDF natively rather than being handed a rasterised
        # page, so a PDF here is the text path; an image upload is not.
        path="text_layer" if mime_type in SUPPORTED_PDF else "vision",
    )
    _check(result)

    logger.info(
        "Extracted %s in %sms: %s lines, %s warnings",
        file_name,
        duration_ms,
        len(result.extracted.get("lines") or []),
        len(result.warnings),
    )
    return result


# ---------------------------------------------------------------------------
# NVIDIA NIM
# ---------------------------------------------------------------------------
#
# OpenAI-compatible chat completions against integrate.backend.nvidia.com.
#
# 🔴 **Read the text layer, do not look at a picture of it.** Your invoices are
# computer-generated PDFs that carry their own text. Rasterising one to an
# image and asking a vision model to squint at it throws away a perfect
# transcript and then asks a model to reconstruct it — and the reconstruction
# is where the errors come from. Measured on a real TEPL invoice, 26 Aug 2026:
#
#   text layer  -> openai/gpt-oss-20b            5s    6/6 fields exact
#   text layer  -> openai/gpt-oss-120b           7s    6/6 fields exact
#   rasterised  -> llama-3.2-90b-vision        158s    every field null
#   rasterised  -> llama-3.2-11b-vision         14s    every field FABRICATED
#
# The 11B result is the one to remember: invoice number "12345" against an
# actual TEPL/2026-27/08, GSTIN "27AAXYZ1234P" against 09AAECS9424P1ZL,
# quantity 10 against 215. It did not fail — it confidently produced a
# complete, fictional invoice. Vision is therefore the fallback for photos and
# scans only, never the path for a document that has text in it.
#
# Two structural differences from the Anthropic path remain:
#
# * **No enforced tool schema.** The reply is prose that should contain JSON,
#   so `_json_from_text` finds and parses it. Strictly weaker than a forced
#   tool call — nothing at the API level guarantees the shape.
# * **A 32,768-token context** on the vision models, which a full-page image
#   exceeds. `_fit_image` downscales until it fits and refuses if it cannot
#   stay legible.

NVIDIA_BASE_URL = "https://integrate.backend.nvidia.com/v1/chat/completions"

#: Below this a PDF's "text" is page furniture, not content — a scan with a
#: few OCR crumbs on it. Anything shorter goes down the vision path.
MIN_TEXT_LAYER_CHARS = 200

#: Below this the invoice text stops being legible to the model at all.
MIN_IMAGE_SCALE = 0.20

NVIDIA_PROMPT = f"""{SYSTEM_PROMPT}

Return ONLY a JSON object with these keys, no prose and no markdown fence:
{{"document_kind":"tax_invoice|proforma_invoice|work_order|purchase_order|other",
 "invoice_no":null,"invoice_date":"YYYY-MM-DD","supplier_name":null,"supplier_gstin":null,
 "buyer_name":null,"buyer_address":null,"buyer_gstin":null,"buyer_pan":null,
 "consignee_name":null,"consignee_address":null,"buyer_order_no":null,
 "work_order_ref":null,"letter_ref":null,"data_link_url":null,
 "tax_rate_pct":null,"tax_kind":"igst|cgst_sgst|none","rate_is_tax_inclusive":false,
 "stated_taxable_value":null,"stated_tax_amount":null,"stated_total":null,
 "lines":[{{"description":"","hsn_sac":null,"quantity":null,
            "unit":"acre|sq_km|hectare|each|lump_sum|day|hour",
            "rate":null,"stated_amount":null,"location_note":null}}],
 "confidence":{{"field_name":0.0}},"notes":null}}

🔴 Two mistakes to avoid, both seen in real readings of these documents:

1. DO NOT SWAP QUANTITY AND RATE. The line table reads
   Sl.No. | Particulars | HSN/SAC | Quantity | Rate | per | Amount.
   `quantity` is the figure under "Quantity"; `rate` is the figure under
   "Rate"; `unit` is the word under "per". On a 2301-acre line at Rs 150 per
   acre, quantity is 2301 and rate is 150 — never the reverse. The product is
   the same either way, so no arithmetic check can catch this for you.

2. SET rate_is_tax_inclusive WHEN THE DOCUMENT SAYS SO. A rate column headed
   "Rate per sq km in rupees (Including GST)" means the tax is already inside
   the rate: set it true and report stated_tax_amount as printed (often 0).
   Missing this overstates revenue by the tax fraction."""


def _fit_image(content: bytes, mime_type: str, *, file_name: str) -> str:
    """
    Base64 an image small enough for the model's context, rasterising a PDF first.

    Returns the encoded JPEG. Raises if the document cannot be made small
    enough while staying legible — better than sending something the model
    will guess at.
    """
    # Reject the file before importing anything to process it with: a missing
    # dependency is our problem to fix, an unsupported upload is the user's, and
    # reporting the first when the second is true sends them to the wrong place.
    if mime_type not in SUPPORTED_PDF | SUPPORTED_IMAGE:
        raise ExtractionError(
            f"{file_name} is a {mime_type} file. Upload a PDF or a photo (JPEG, PNG, WebP)."
        )

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - environment problem
        raise ExtractionError("Pillow is not installed. Run: pip install pillow") from exc

    if mime_type in SUPPORTED_PDF:
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise ExtractionError(
                "This provider cannot read PDFs directly, and pypdfium2 is not "
                "installed to convert one. Run: pip install pypdfium2 — or upload "
                "a photo of the invoice instead."
            ) from exc
        pdf = pdfium.PdfDocument(io.BytesIO(content))
        if len(pdf) == 0:
            raise ExtractionError(f"{file_name} has no pages.")
        image = pdf[0].render(scale=2.0).to_pil()
    else:  # an image — the guard above left nothing else it can be
        image = Image.open(io.BytesIO(content)).convert("RGB")

    width, height = image.size
    for scale, quality in ((0.34, 55), (0.28, 48), (0.24, 45), (MIN_IMAGE_SCALE, 40)):
        buf = io.BytesIO()
        image.resize((int(width * scale), int(height * scale)), Image.LANCZOS).save(
            buf, "JPEG", quality=quality, optimize=True
        )
        encoded = base64.b64encode(buf.getvalue()).decode()
        # 🔴 Measured, not guessed. A 180KB payload of this document billed
        # 32,436 message tokens against a 32,768 limit and was rejected; the
        # budget has to leave room for the prompt and the completion too.
        # Tokens track pixel count rather than compressed bytes, so this is a
        # conservative proxy — err small, since a rejected request reads to the
        # user as "the reader is broken".
        if len(encoded) <= 60_000:
            return encoded

    raise ExtractionError(
        f"{file_name} is too detailed for this provider's context window even after "
        f"downscaling. Upload a single page, or switch "
        f"INVOICE_EXTRACTION_PROVIDER to 'anthropic'."
    )


def _json_from_text(text: str) -> dict[str, Any]:
    """
    Pull the JSON object out of a prose response.

    Necessary because this provider has no forced-tool-call equivalent. Three
    things the reply can do, all seen in testing:

    * wrap the object in a markdown fence;
    * put a sentence in front of it;
    * **be a reasoning model** that writes out its working first — including a
      quoted copy of the invoice, braces and all — and only then answers.

    That last one is why this scans for the *last* balanced object carrying the
    keys we asked for, rather than taking the first ``{`` through to the last
    ``}``. The naive version on a reasoning model's output yields either a
    parse error or, worse, the model's scratch work presented as an invoice.
    """
    if not text:
        raise ExtractionError(
            "The document reader returned an empty reply. Try again, or fill the form by hand."
        )

    # Some models emit an explicit reasoning block. Drop it outright.
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    best: dict[str, Any] | None = None
    for match in re.finditer(r"\{", cleaned):
        depth = 0
        for i in range(match.start(), len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        candidate = json.loads(cleaned[match.start() : i + 1])
                    except json.JSONDecodeError:
                        pass
                    else:
                        # 🔴 `lines` must be a *list*, not merely present. The
                        # `confidence` block also carries a "lines" key — with
                        # a float score against it — so testing for the key
                        # alone picks the confidence map and reports 0.99 as
                        # the invoice number.
                        if isinstance(candidate, dict) and isinstance(candidate.get("lines"), list):
                            best = candidate
                    break

    if best is not None:
        return best

    # Nothing with lines in it. Fall back to any object at all, so a reply that
    # read the header but found no line items still half-fills the form.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ExtractionError("The model did not return a readable invoice. Fill the form by hand.")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ExtractionError(
            f"The model's reply could not be parsed: {exc}. Fill the form by hand."
        ) from exc


def _pdf_text(content: bytes) -> str:
    """
    The PDF's own text layer, or "" if it has none worth reading.

    A computer-generated invoice carries a perfect transcript of itself. Using
    it is lossless — there is no OCR step to get a digit wrong in.
    """
    try:
        from pypdf import PdfReader
    except ImportError as error:  # pragma: no cover - a deployment fault
        # 🔴 Refuse, do not fall back to vision.
        #
        # These are two different situations and only one of them is safe:
        #
        #   * A PDF with no text layer is a scan, and vision is the right and
        #     only path for it.
        #   * A PDF whose text layer we cannot *read* because a package is
        #     missing is a deployment fault — and quietly rasterising it sends
        #     a perfect transcript down the path INVOICE.md measured
        #     fabricating an entire invoice (number "12345" against an actual
        #     TEPL/2026-27/08).
        #
        # Returning "" made those indistinguishable. An install problem must
        # not degrade into a correctness problem, so this says what is wrong
        # and stops.
        raise ExtractionError(
            "This server cannot read the text layer of a PDF: pypdf is not "
            "installed. Run `pip install -r api/requirements.txt`. "
            "Refusing to fall back to reading the page as an image — that path "
            "is for scans and photographs, and using it on a document that "
            "carries its own text is how a model invents figures."
        ) from error

    try:
        reader = PdfReader(io.BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        logger.warning("Could not read a text layer; falling back to the vision path")
        return ""

    return text if len(text.strip()) >= MIN_TEXT_LAYER_CHARS else ""


def _nvidia_call(*, model: str, content: str, max_tokens: int, file_name: str) -> tuple[dict, int]:
    """POST to the completions endpoint and return (body, duration_ms)."""
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - environment problem
        raise ExtractionError("requests is not installed. Run: pip install requests") from exc

    started = time.monotonic()
    try:
        response = requests.post(
            NVIDIA_BASE_URL,
            headers={
                "Authorization": f"Bearer {settings.nvidia_api_key}",
                "Accept": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": max_tokens,
                # Zero, not the default. Reading a document is transcription;
                # nothing here benefits from sampling variety.
                "temperature": 0.0,
            },
            timeout=settings.nvidia_timeout_seconds,
        )
    except requests.exceptions.Timeout as exc:
        raise ExtractionError(
            f"The document reader did not respond within "
            f"{settings.nvidia_timeout_seconds}s. Try again, or fill the form by hand."
        ) from exc
    except requests.exceptions.RequestException as exc:
        logger.exception("NVIDIA extraction failed for %s", file_name)
        raise ExtractionError(f"The document could not be read: {exc}") from exc

    duration_ms = int((time.monotonic() - started) * 1000)

    if response.status_code != 200:
        logger.error("NVIDIA extraction HTTP %s: %s", response.status_code, response.text[:400])

        # 🔴 410 means the hosted model was retired, and it is worth its own
        # message. A provider ages models out on a published date, so a
        # deployment that worked on Tuesday returns "Gone" on Wednesday with
        # nothing having changed on our side. "HTTP 410. Fill the form by
        # hand." sends an operator looking for a bug in the upload; naming the
        # model and the date sends them to one line of configuration.
        if response.status_code == 410:
            detail = ""
            try:
                detail = str(response.json().get("detail", ""))
            except ValueError:
                detail = response.text[:200]
            raise ExtractionError(
                f"The configured document-reading model '{model}' has been "
                f"retired by the provider and is no longer available. "
                f"{detail} Set NVIDIA_TEXT_MODEL (or NVIDIA_VISION_MODEL) to a "
                f"current model and restart. Nothing is wrong with your file."
            )

        raise ExtractionError(
            f"The document reader returned HTTP {response.status_code}. Fill the form by hand."
        )
    return response.json(), duration_ms


def _extract_nvidia(
    content: bytes, *, file_name: str, mime_type: str, model: str
) -> ExtractionResult:
    """
    NVIDIA-hosted open-weights model, via the OpenAI-compatible API.

    Two paths, and which one runs matters more than which model is configured:

    * **Text** — a PDF with a text layer. Lossless, fast (5-8s), and measured
      exact on a real invoice. This is what your own documents take.
    * **Vision** — a photo or a scan, where there is no text to read. Slower,
      far less reliable, and always flagged for review.
    """
    if not settings.nvidia_api_key:
        raise ExtractionError(
            "Document reading is not configured on this server. "
            "Set NVIDIA_API_KEY, or fill the form by hand."
        )

    text_layer = _pdf_text(content) if mime_type in SUPPORTED_PDF else ""

    if text_layer:
        used_model, via_vision = settings.nvidia_text_model, False
        prompt_content = f"{NVIDIA_PROMPT}\n\nINVOICE TEXT:\n{text_layer}"
        # Generous, because a reasoning model spends most of this budget
        # thinking before it emits the object.
        max_tokens = 3000
    else:
        used_model, via_vision = model, True
        encoded = _fit_image(content, mime_type, file_name=file_name)
        # This provider takes the image inline in the message text rather than
        # as a separate content block.
        prompt_content = f'{NVIDIA_PROMPT} <img src="data:image/jpeg;base64,{encoded}" />'
        max_tokens = 1024

    body, duration_ms = _nvidia_call(
        model=used_model,
        content=prompt_content,
        max_tokens=max_tokens,
        file_name=file_name,
    )

    def parse(response_body: dict) -> dict[str, Any]:
        # A reasoning model may leave `content` null and put everything in
        # `reasoning_content`, so take whichever one has text in it.
        message = response_body["choices"][0].get("message") or {}
        return _normalise(
            _json_from_text(message.get("content") or message.get("reasoning_content") or "")
        )

    try:
        payload = parse(body)
    except ExtractionError:
        # 🔴 One retry, and only one. Without an enforced tool schema the model
        # occasionally answers in a shape nothing can parse — measured roughly
        # one reply in several on the same document. A single stricter re-ask
        # fixes almost all of them; retrying in a loop against a model that
        # cannot do the job just burns the user's time and quota.
        logger.warning("Unparseable reply for %s; re-asking once", file_name)
        body, retry_ms = _nvidia_call(
            model=used_model,
            content=(
                f"{prompt_content}\n\n"
                "Your previous reply could not be parsed. Reply with the JSON "
                "object ONLY — no explanation, no reasoning, no markdown fence."
            ),
            max_tokens=max_tokens,
            file_name=file_name,
        )
        duration_ms += retry_ms
        payload = parse(body)

    result = ExtractionResult(
        extracted=payload,
        confidence={
            k: float(v)
            for k, v in (payload.get("confidence") or {}).items()
            if isinstance(v, int | float)
        },
        raw=body,
        model=used_model,
        duration_ms=duration_ms,
        path="vision" if via_vision else "text_layer",
    )

    if via_vision:
        # 🔴 An open-weights vision model reading a dense tax invoice is
        # materially less reliable, and one was measured fabricating every
        # field. Force review whatever it claims about its own confidence — a
        # fabricated reading is confident by construction.
        result.warnings.append(
            "Read from a photograph rather than a text layer. Check every figure "
            "against the original document before issuing."
        )

    logger.info(
        "Extracted %s via %s in %sms (%s)",
        file_name,
        used_model,
        duration_ms,
        "vision" if via_vision else "text layer",
    )
    return result


def to_draft_payload(result: ExtractionResult, *, entity_code: str) -> dict[str, Any]:
    """
    Shape an extraction into the create-invoice form's fields.

    Note what is *not* carried over: no invoice number (allocation happens at
    issue), no totals (recomputed), no tax treatment (§5.4 is open). The form
    arrives filled in with what was read and blank where a decision is owed.
    """
    data = result.extracted
    buyer_gstin = data.get("buyer_gstin")

    return {
        "entity_code": entity_code,
        "invoice_date": data.get("invoice_date"),
        "buyer_name": data.get("buyer_name"),
        "buyer_address": data.get("buyer_address"),
        "buyer_gstin": buyer_gstin,
        "buyer_pan": data.get("buyer_pan"),
        "buyer_state_code": gstin_lib.state_code(buyer_gstin or ""),
        "consignee_name": data.get("consignee_name"),
        "consignee_address": data.get("consignee_address"),
        "buyer_order_no": data.get("buyer_order_no"),
        "work_order_ref": data.get("work_order_ref"),
        "letter_ref": data.get("letter_ref"),
        "data_link_url": data.get("data_link_url"),
        "tax_rate_pct": data.get("tax_rate_pct") or 18,
        "lines": [
            {
                "line_no": i,
                "description": line.get("description", ""),
                "hsn_sac": line.get("hsn_sac"),
                "quantity": line.get("quantity"),
                "unit": line.get("unit") or "acre",
                "rate": line.get("rate"),
                "rate_is_tax_inclusive": bool(data.get("rate_is_tax_inclusive")),
                "location_note": line.get("location_note"),
            }
            for i, line in enumerate(data.get("lines") or [], 1)
        ],
        "_warnings": result.warnings,
        "_confidence": result.confidence,
        "_needs_review": result.needs_review,
        "_notes": data.get("notes"),
    }
