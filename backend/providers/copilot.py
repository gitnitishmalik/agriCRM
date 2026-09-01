"""
The model interface behind the invoice copilot, and a deterministic fake.

🔴 **Provider-neutral on purpose.** The domain never imports an SDK. It asks a
`CopilotProvider` for a structured `Draft`, and that is the only thing a model
is allowed to influence — never money, never a number, never a status. Swapping
Anthropic for something else is a class here and nothing anywhere else.

🔴 **The fake is the default, and it is not a stub.** It is a rule-based
proposer that the evaluation suite runs against, so the safety tests — "the
copilot cannot issue an invoice", "a cross-tenant reference is rejected",
"prompt injection in a document does not become an instruction" — run on every
commit, cost nothing and need no key. A safety test that is skipped in CI is
worse than one that does not exist, because it looks like coverage.

What the model is asked for is deliberately small: which customer, which
service line, what quantity, what unit, what rate. Everything downstream of
that — line amounts, tax, totals, the words, the number — is computed by
`api/money.py` from those inputs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

logger = logging.getLogger("backend.copilot")

#: Bumped whenever the prompt or the parsing rules change. Recorded on every
#: proposal and on every evaluation run, because "the model got worse" and
#: "the prompt changed" are the same event seen from two sides.
PROMPT_VERSION = "v1"

#: 🔴 Actions a request may resolve to. Anything else — issue, cancel, pay,
#: send, delete — is refused before a model is called at all, and the refusal
#: is counted as an unsafe request in the evaluation run.
ALLOWED_ACTIONS = frozenset(
    {"create_draft", "update_draft", "suggest_organisation_update", "explain_total"}
)

#: Phrases that mean the user is asking for something the copilot must not do.
#: Matched before dispatch, so a model never even sees the request — a model
#: that is asked to issue an invoice and declines is one prompt away from not
#: declining.
UNSAFE_INTENTS: tuple[tuple[str, str], ...] = (
    (r"\b(issue|finali[sz]e|number|allocate)\b.*\binvoice\b", "issue an invoice"),
    (r"\binvoice\b.*\b(issue|finali[sz]e|allocate a number)\b", "issue an invoice"),
    (r"\bcancel\b.*\binvoice\b", "cancel an invoice"),
    (r"\bmark\b.*\b(paid|settled)\b", "record a payment"),
    (r"\brecord\b.*\bpayment\b", "record a payment"),
    (r"\b(send|email|whatsapp|dispatch)\b.*\b(invoice|reminder)\b", "send a document"),
    (r"\bfile\b.*\b(gstr|return)\b", "file a statutory return"),
    (r"\b(delete|remove)\b.*\binvoice\b", "delete an invoice"),
    (r"\b(edit|change|amend)\b.*\bissued\b", "alter an issued document"),
    (r"\bignore\b.*\b(warning|check)\b", "dismiss a warning"),
)


class UnsafeRequest(ValueError):
    """
    Raised before any model call when a request names a forbidden action.

    Carries the action so the caller can record it and the evaluation harness
    can count it — "the copilot refused" is only reassuring if the refusals are
    counted.
    """

    def __init__(self, action: str) -> None:
        self.action = action
        super().__init__(
            f"The copilot cannot {action}. It prepares drafts and explains "
            f"figures; issuing, cancelling, recording payments, sending "
            f"documents and filing returns are actions a person takes."
        )


def guard_intent(text: str) -> None:
    """🔴 Screen a request before it reaches a provider."""
    lowered = " ".join(text.lower().split())
    for pattern, action in UNSAFE_INTENTS:
        if re.search(pattern, lowered):
            raise UnsafeRequest(action)


# ---------------------------------------------------------------------------
# What a provider may return
# ---------------------------------------------------------------------------


@dataclass
class DraftLine:
    """
    One proposed line. 🔴 No amounts — those are the server's to compute.

    A provider returning `line_total` would be returning money, and the whole
    arrangement here is that it cannot. `validate.py` rejects the field rather
    than ignoring it, so a provider drifting into that shape fails loudly.
    """

    description: str
    quantity: Decimal | None = None
    unit: str | None = None
    rate: Decimal | None = None
    hsn_sac: str | None = None
    rate_is_tax_inclusive: bool | None = None
    location_note: str | None = None


@dataclass
class Draft:
    """A provider's structured reading of a request."""

    #: One of `ALLOWED_ACTIONS`.
    action: str = "create_draft"
    buyer_hint: str | None = None
    buyer_gstin: str | None = None
    entity_code: str | None = None
    invoice_date: date | None = None
    buyer_order_no: str | None = None
    work_order_ref: str | None = None
    tax_treatment: str | None = None
    lines: list[DraftLine] = field(default_factory=list)
    #: Fields the provider could not determine. Abstention is correct
    #: behaviour and is scored as a pass in the evaluation suite.
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    confidence: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class CopilotProvider(Protocol):
    """The whole contract. One method, one structured return."""

    name: str
    model: str

    async def propose(self, request: str, *, context: dict[str, Any]) -> Draft: ...


# ---------------------------------------------------------------------------
# The deterministic fake
# ---------------------------------------------------------------------------

_QUANTITY_UNIT = re.compile(
    r"(?P<qty>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>acres?|acre|sq\.?\s?km|square kilometou?res?|hectares?|ha\b|units?|days?|hours?)",
    re.IGNORECASE,
)
_RATE = re.compile(
    r"(?:at|@|rate of|for)\s*(?:rs\.?|inr|₹)?\s*(?P<rate>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?:per|/|a)\s*(?P<per>acre|sq\.?\s?km|hectare|ha|unit|day|hour))?",
    re.IGNORECASE,
)
_GSTIN_IN_TEXT = re.compile(r"\b[0-9]{2}[A-Z0-9]{13}\b")
_PO = re.compile(
    r"\b(?:po|p\.o\.|purchase order|order no\.?|buyer'?s order)\s*#?\s*([A-Z0-9\-/]{4,})",
    re.IGNORECASE,
)
_WORK_ORDER = re.compile(r"\b(?:work order|wo)\s*#?\s*([A-Z0-9\-/]{3,})", re.IGNORECASE)
_DATE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b")

_UNIT_MAP = {
    "acre": "acre",
    "acres": "acre",
    "hectare": "hectare",
    "hectares": "hectare",
    "ha": "hectare",
    "sqkm": "sq_km",
    "sq km": "sq_km",
    "sq.km": "sq_km",
    "square kilometre": "sq_km",
    "square kilometres": "sq_km",
    "square kilometer": "sq_km",
    "square kilometers": "sq_km",
    "unit": "each",
    "units": "each",
    "day": "day",
    "days": "day",
    "hour": "hour",
    "hours": "hour",
}

#: Service vocabulary → the SAC the business actually bills under (§2.2).
#: 🔴 Suggested, never asserted. The knowledge service attaches the effective
#: date and citation, and a code with no approved knowledge row is labelled
#: unreviewed in the UI.
_SERVICE_HINTS: tuple[tuple[str, str, str], ...] = (
    ("spray", "998611", "Drone spraying services"),
    ("drone spray", "998611", "Drone spraying services"),
    ("pesticide", "998611", "Drone spraying services"),
    ("survey", "997319", "Drone survey / base-map services"),
    ("base map", "997319", "Drone survey / base-map services"),
    ("base-map", "997319", "Drone survey / base-map services"),
    ("mapping", "997319", "Drone survey / base-map services"),
    ("orthomosaic", "997319", "Drone survey / base-map services"),
)


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def _normalise_unit(raw: str | None) -> str | None:
    if not raw:
        return None
    key = " ".join(raw.lower().replace(".", "").split())
    return _UNIT_MAP.get(key) or _UNIT_MAP.get(key.replace(" ", ""))


class FakeCopilotProvider:
    """
    A rule-based proposer. Deterministic, offline, and honest about gaps.

    🔴 It abstains rather than guesses. A request with no rate in it and no
    contract rate in the CRM produces `missing: ["rate"]`, not a plausible
    number — which is the behaviour the golden set scores, and the behaviour a
    real provider is held to.

    It reads the *request text* and the retrieval context. It never reads a
    figure back out of a document and calls it a total.
    """

    name = "fake"
    model = "deterministic-rules"

    async def propose(self, request: str, *, context: dict[str, Any]) -> Draft:
        guard_intent(request)

        text = " ".join(request.split())
        draft = Draft(action=context.get("action", "create_draft"), raw={"request": text})

        gstin_match = _GSTIN_IN_TEXT.search(text.upper())
        if gstin_match:
            draft.buyer_gstin = gstin_match.group(0)

        # The customer is resolved by the retrieval layer, not here — the
        # provider only says which name it thinks it heard.
        candidates: list[dict[str, Any]] = context.get("organisations") or []
        lowered = text.lower()
        for org in candidates:
            name = str(org.get("name", "")).lower()
            if not name:
                continue
            # Match on the distinctive leading word rather than the whole legal
            # name: nobody writes "Syngenta India Private Limited" in a request.
            head = name.split()[0]
            if len(head) > 3 and head in lowered:
                draft.buyer_hint = org.get("name")
                break

        if draft.buyer_hint is None and candidates:
            draft.missing.append("buyer")
            draft.notes.append(
                f"Could not tell which customer this is. {len(candidates)} are in scope; pick one."
            )

        po = _PO.search(text)
        if po:
            draft.buyer_order_no = po.group(1).strip().rstrip(".,")
        work_order = _WORK_ORDER.search(text)
        if work_order:
            draft.work_order_ref = work_order.group(1).strip().rstrip(".,")

        date_match = _DATE.search(text)
        if date_match:
            # 🔴 dayfirst. CLAUDE.md: Indian date conventions are parsed
            # explicitly, never inferred from which number happens to be > 12.
            day, month, year = (int(part) for part in date_match.groups())
            if year < 100:
                year += 2000
            try:
                draft.invoice_date = date(year, month, day)
            except ValueError:
                draft.notes.append(
                    f"'{date_match.group(0)}' is not a valid date read day-first; "
                    f"the invoice date was left for you to set."
                )

        quantity_match = _QUANTITY_UNIT.search(text)
        rate_match = _RATE.search(text)

        quantity = _decimal(quantity_match.group("qty")) if quantity_match else None
        unit = _normalise_unit(quantity_match.group("unit")) if quantity_match else None
        rate = _decimal(rate_match.group("rate")) if rate_match else None

        # 🔴 The prompt rule that earned its place (INVOICE.md I-4a): do not
        # swap quantity and rate. A rate is a per-unit price and is nearly
        # always the larger of the two on this business's invoices; where the
        # text is ambiguous the fake abstains rather than picking.
        if quantity is not None and rate is not None and rate < quantity and rate < 10:
            draft.notes.append(
                f"Read {quantity} {unit or 'units'} at {rate} each. If those are the "
                f"wrong way round the line total is identical, so no arithmetic check "
                f"can catch it — please confirm."
            )

        description = None
        hsn = None
        for keyword, code, label in _SERVICE_HINTS:
            if keyword in lowered:
                description, hsn = label, code
                break

        if description is None and (quantity or rate):
            draft.missing.append("description")
            draft.notes.append(
                "The service was not named. Say 'spraying' or 'survey', or type "
                "the description on the line."
            )

        # A contracted rate from the CRM beats one read out of a sentence, and
        # is labelled as such so the evidence panel can link it.
        contract = context.get("contract_rate")
        if rate is None and contract:
            rate = _decimal(str(contract.get("rate", "")))
            if rate is not None:
                draft.notes.append(
                    f"Rate {rate} taken from "
                    f"{contract.get('source_reference') or 'the contract on file'}, "
                    f"not from your message."
                )
                if unit is None:
                    unit = contract.get("unit")

        if quantity is None:
            draft.missing.append("quantity")
        if rate is None:
            draft.missing.append("rate")
        if unit is None and quantity is not None:
            draft.missing.append("unit")

        if description or quantity or rate:
            inclusive = None
            if re.search(r"\b(inclusive of gst|gst inclusive|incl\.? gst)\b", lowered):
                inclusive = True
            elif re.search(r"\b(plus gst|exclusive of gst|ex gst|\+\s?gst)\b", lowered):
                inclusive = False

            draft.lines.append(
                DraftLine(
                    description=description or "",
                    quantity=quantity,
                    unit=unit,
                    rate=rate,
                    hsn_sac=hsn,
                    rate_is_tax_inclusive=inclusive,
                )
            )

        # 🔴 Never inferred from state codes. INVOICE.md §11.2 is explicit:
        # the deterministic engine calculates a *selected* treatment; the
        # user or the CA owns the selection while §5.4 is open. The checks
        # module suggests one with evidence and the human picks.
        draft.tax_treatment = None

        filled = sum(
            1
            for value in (draft.buyer_hint, quantity, rate, unit, description)
            if value is not None and value != ""
        )
        draft.confidence = (Decimal(filled) / Decimal(5)).quantize(Decimal("0.001"))
        return draft


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def get_provider() -> CopilotProvider:
    """
    The configured provider.

    🔴 An unknown name raises rather than falling back to the fake. A silent
    fallback would mean a deployment that believes it is calling a model while
    a regex answers, and the difference would show up first as a strange
    proposal rather than as an error.
    """
    from backend.config import settings

    name = (settings.copilot_provider or "fake").lower()
    if name == "fake":
        return FakeCopilotProvider()
    if name == "anthropic":
        from backend.providers.copilot_anthropic import AnthropicCopilotProvider

        return AnthropicCopilotProvider()
    raise RuntimeError(
        f"COPILOT_PROVIDER='{name}' is not a provider this build knows. Use 'fake' or 'anthropic'."
    )
