"""
Stage 2 of the ingestion pipeline — NORMALISE (Doc 06 §1).

Pure functions, no database, no session. That is deliberate: these are the
rules most likely to be wrong in a way nobody notices for months, so they have
to be cheap to test exhaustively. Every one of them returns a value or raises
`NormaliseError`; none of them guesses.

🔴 The theme running through this module is **reject rather than guess**. A
row rejected at import is a line in an error report somebody fixes that
afternoon. A row guessed wrong is a landholding out by a factor of four, a
`farmer_class` derived from it, a segment built on that, and a project sized
from the segment — discovered, if ever, a year later with no way to tell which
import did it.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from backend.domain.pii import NormalisationError, normalise_email, normalise_phone


class NormaliseError(ValueError):
    """A value that cannot be stored without guessing at its meaning."""

    def __init__(self, message: str, *, code: str = "unparseable") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Area — 🔴 the one Doc 06 calls out as silently catastrophic
# ---------------------------------------------------------------------------

#: 🔴 A bigha is not a unit. It is a family of units that share a name.
#:
#: West Bengal ~0.1338 ha, Assam ~0.1338, Bihar ~0.2529, UP varies by district
#: between ~0.2529 and ~0.6772, Uttarakhand ~0.16, Rajasthan ~0.2529 (pucca)
#: or ~0.1012 (kaccha), MP/Gujarat ~0.1618, Punjab/Haryana ~0.2529.
#:
#: The spread is a factor of six. Doc 06: "Guessing produces landholding data
#: that is silently wrong by a factor of four." So this table is keyed by state
#: name and an unknown state is an error, never a default. Rajasthan and UP
#: carry an explicit `ambiguous` marker because a single state-level number is
#: itself a guess there.
BIGHA_HA: dict[str, Decimal] = {
    "west bengal": Decimal("0.1338"),
    "assam": Decimal("0.1338"),
    "tripura": Decimal("0.1338"),
    "bihar": Decimal("0.2529"),
    "jharkhand": Decimal("0.2529"),
    "punjab": Decimal("0.2529"),
    "haryana": Decimal("0.2529"),
    "himachal pradesh": Decimal("0.1012"),
    "uttarakhand": Decimal("0.1600"),
    "madhya pradesh": Decimal("0.1618"),
    "gujarat": Decimal("0.1618"),
}

#: States where "bigha" has no single agreed value. Naming them separately is
#: the honest answer: the importer must be given acres or hectares instead.
BIGHA_AMBIGUOUS = frozenset({"uttar pradesh", "rajasthan"})

#: Units with one unambiguous conversion.
AREA_HA: dict[str, Decimal] = {
    "ha": Decimal(1),
    "hectare": Decimal(1),
    "hectares": Decimal(1),
    "acre": Decimal("0.404686"),
    "acres": Decimal("0.404686"),
    "ac": Decimal("0.404686"),
    "guntha": Decimal("0.010117"),
    "gunta": Decimal("0.010117"),
    "cent": Decimal("0.004047"),
    "sq_km": Decimal(100),
    "sqkm": Decimal(100),
    "km2": Decimal(100),
    "sq_m": Decimal("0.0001"),
    "sqm": Decimal("0.0001"),
    "kanal": Decimal("0.0505857"),
    "marla": Decimal("0.00252929"),
}

#: Doc 06 §1 stage 3 — range validation for a landholding, in hectares.
AREA_MIN_HA = Decimal("0.01")
AREA_MAX_HA = Decimal(5000)


def area_to_hectares(
    value: str | float | Decimal, unit: str, *, state: str | None = None
) -> Decimal:
    """
    Convert a declared area to hectares, or raise.

    🔴 `state` is required for bigha and ignored otherwise. Calling this with
    `unit="bigha"` and no state raises rather than picking a middle value —
    CLAUDE.md: "use a state-keyed table, reject rather than guess".
    """
    try:
        amount = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, AttributeError) as error:
        raise NormaliseError(f"{value!r} is not a number.", code="area_unparseable") from error

    if amount <= 0:
        raise NormaliseError(f"Area must be positive, got {amount}.", code="area_range")

    key = unit.strip().lower().replace(" ", "_")

    if key in {"bigha", "beegha", "bigah"}:
        if state is None:
            raise NormaliseError(
                "A bigha has no fixed size — it ranges from ~0.10 ha to ~0.68 ha "
                "by state. Supply the state, or give the area in acres or hectares.",
                code="area_bigha_no_state",
            )
        normalised_state = state.strip().lower()
        if normalised_state in BIGHA_AMBIGUOUS:
            raise NormaliseError(
                f"A bigha in {state} varies by district, so there is no single "
                f"conversion to apply. Supply the area in acres or hectares.",
                code="area_bigha_ambiguous",
            )
        factor = BIGHA_HA.get(normalised_state)
        if factor is None:
            raise NormaliseError(
                f"No bigha conversion is recorded for {state}. Supply the area "
                f"in acres or hectares, or add the state to BIGHA_HA with a "
                f"cited source.",
                code="area_bigha_unknown_state",
            )
    else:
        factor = AREA_HA.get(key)
        if factor is None:
            raise NormaliseError(
                f"Unknown area unit {unit!r}. Known units: "
                f"{', '.join(sorted(AREA_HA))}, bigha (state-keyed).",
                code="area_unknown_unit",
            )

    hectares = (amount * factor).quantize(Decimal("0.0001"))

    if not (AREA_MIN_HA <= hectares <= AREA_MAX_HA):
        raise NormaliseError(
            f"{amount} {unit} is {hectares} ha, outside the plausible range "
            f"{AREA_MIN_HA}–{AREA_MAX_HA} ha for a landholding.",
            code="area_range",
        )
    return hectares


# ---------------------------------------------------------------------------
# Dates — 🔴 dayfirst, never inferred
# ---------------------------------------------------------------------------

_DATE_FORMATS = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%d-%m-%y",
    "%Y-%m-%d",  # ISO, unambiguous
    "%d %b %Y",
    "%d %B %Y",
)


def parse_date(value: str) -> dt.date:
    """
    Parse an Indian-convention date. **Day first, always.**

    🔴 `03/04/2026` is 3 April, never 3 March. Doc 06 states this and the
    reason it is stated rather than assumed: a parser that infers the order
    from the data will read `03/04` as March in a file whose first ambiguous
    row happens to be American, and then read the whole column that way.

    ISO `YYYY-MM-DD` is accepted because it cannot be ambiguous.
    """
    text = str(value).strip()
    if not text:
        raise NormaliseError("Empty date.", code="date_empty")

    for fmt in _DATE_FORMATS:
        try:
            # A birth date or registration date is a calendar date, not an
            # instant. Attaching a timezone would invent a precision the
            # source does not have, so `.date()` discards it deliberately.
            parsed = dt.datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
        # A two-digit year that lands in the future is almost always last
        # century — a farmer born in '68, not one born in 2068.
        if "%y" in fmt and parsed.year > dt.date.today().year:
            parsed = parsed.replace(year=parsed.year - 100)
        return parsed

    raise NormaliseError(
        f"{value!r} is not a date this importer recognises. Use DD/MM/YYYY or YYYY-MM-DD.",
        code="date_unparseable",
    )


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

#: Tokens that keep their case. Title-casing these produces "Md", "Fpo", "Ltd"
#: — which then differ from every other row and break exact-match dedupe.
_NAME_KEEP_UPPER = frozenset(
    {
        "MD",
        "CEO",
        "FPO",
        "FPC",
        "ACS",
        "LTD",
        "PVT",
        "LLP",
        "NGO",
        "SHG",
        "JLG",
        "II",
        "III",
        "IV",
    }
)

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def is_devanagari(value: str) -> bool:
    """Whether a string carries Devanagari, and so belongs in `name_local`."""
    return bool(_DEVANAGARI.search(value))


def normalise_name(value: str) -> str:
    """
    Trim, collapse whitespace, Title Case with an exception list.

    🔴 Devanagari is returned verbatim. Title-casing a Devanagari string is a
    no-op at best and a mangling at worst, and CLAUDE.md requires `name_local`
    preserved exactly as supplied.
    """
    text = " ".join(str(value).split())
    if not text:
        raise NormaliseError("Empty name.", code="name_empty")
    if is_devanagari(text):
        return text

    parts = []
    for token in text.split(" "):
        bare = token.strip(".,")
        if bare.upper() in _NAME_KEEP_UPPER:
            parts.append(token.upper())
        elif len(bare) <= 2 and bare.isupper():
            # Initials — "R." stays "R.", not "R.".title() nonsense.
            parts.append(token)
        else:
            parts.append(token.capitalize())
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Money, booleans, CIN
# ---------------------------------------------------------------------------

_LAKH = Decimal(100_000)
_CRORE = Decimal(10_000_000)


def parse_money(value: str | float | Decimal) -> Decimal:
    """
    Strip `₹`, commas and Lakh/Crore suffixes to a plain rupee amount.

    Indian sheets carry "₹ 12,50,000", "12.5 Lakh" and "1.25 Cr" in the same
    column, and all three mean the same number.
    """
    text = str(value).strip().replace("₹", "").replace(",", "").strip()
    if not text:
        raise NormaliseError("Empty amount.", code="money_empty")

    multiplier = Decimal(1)
    lowered = text.lower()
    for suffix, factor in (
        ("crore", _CRORE),
        ("crores", _CRORE),
        ("cr", _CRORE),
        ("lakh", _LAKH),
        ("lakhs", _LAKH),
        ("lac", _LAKH),
        ("lacs", _LAKH),
    ):
        if lowered.endswith(suffix):
            multiplier = factor
            text = text[: -len(suffix)].strip()
            break

    try:
        return (Decimal(text) * multiplier).quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise NormaliseError(f"{value!r} is not an amount.", code="money_unparseable") from error


_TRUE = frozenset({"y", "yes", "true", "1", "t", "हाँ", "हा", "haan"})
_FALSE = frozenset({"n", "no", "false", "0", "f", "नहीं", "nahi", "nahin"})


def parse_bool(value: str | bool | int) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise NormaliseError(f"{value!r} is not a yes/no value.", code="bool_unparseable")


#: 21 characters: listing status, 5-digit industry code, 2-letter state,
#: 4-digit year, 3-letter ownership, 6-digit registration number.
_CIN = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")


def normalise_cin(value: str) -> str:
    """Uppercase, strip spaces, validate the 21-character MCA format."""
    text = str(value).upper().replace(" ", "").replace("-", "").strip()
    if not _CIN.match(text):
        raise NormaliseError(f"{value!r} is not a valid 21-character CIN.", code="cin_invalid")
    return text


# ---------------------------------------------------------------------------
# Contact — delegated, so there is one phone rule in the codebase
# ---------------------------------------------------------------------------


def normalise_contact(kind: str, value: str) -> str:
    """
    🔴 Delegates to `domain.pii`. There is exactly one phone normalisation
    rule in this codebase and this is not a second copy of it — an importer
    that normalised phones its own way would produce numbers the rest of the
    system cannot match against `value_normalised`.
    """
    try:
        if kind == "email":
            return normalise_email(value)
        return normalise_phone(value)
    except NormalisationError as error:
        raise NormaliseError(str(error), code=f"{kind}_invalid") from error


#: Role addresses belong on an organisation, not on a person (Doc 06 §1).
ROLE_EMAIL_LOCALS = frozenset(
    {"info", "admin", "contact", "office", "support", "sales", "enquiry", "help"}
)


def is_role_email(value: str) -> bool:
    return value.split("@", 1)[0].strip().lower() in ROLE_EMAIL_LOCALS


__all__ = [
    "AREA_HA",
    "AREA_MAX_HA",
    "AREA_MIN_HA",
    "BIGHA_AMBIGUOUS",
    "BIGHA_HA",
    "ROLE_EMAIL_LOCALS",
    "NormaliseError",
    "area_to_hectares",
    "is_devanagari",
    "is_role_email",
    "normalise_cin",
    "normalise_contact",
    "normalise_name",
    "parse_bool",
    "parse_date",
    "parse_money",
]
