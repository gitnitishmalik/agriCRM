"""
Money, area and tax arithmetic for invoices.

Pure functions, no Django, no database. Everything an invoice prints as a
number comes from here, so this is the one module in the billing app that has
to be exactly right — a rounding decision made differently in two places is how
a printed line table stops summing to its own total, and a customer's accounts
team rejects the document over a rupee.

Three rules run through all of it:

1. **Decimal, never float.** ``0.1 + 0.2`` is not ``0.3`` in binary floating
   point, and an invoice is not the place to discover that. Every value that
   will be printed or stored is a ``Decimal`` from the moment it enters.

2. **Round per line, then sum.** Not sum-then-round. The printed table shows
   each line rounded to paise; the total has to be the sum of the numbers the
   reader can see, not a more precise number that disagrees with them.

3. **Bankers' rounding is wrong here.** Python's default ``ROUND_HALF_EVEN``
   sends 2.5 to 2 and 3.5 to 4. Indian invoicing convention, and every
   accounting package your CA will reconcile against, uses ``ROUND_HALF_UP``.

Moved verbatim from `apps/billing/money.py` during the FastAPI
migration — it had no Django references at all, so rewriting it would
have been an opportunity to introduce a rounding difference for nothing.
🔴 If the Django service is still running, the two copies must stay
identical; `api/tests/test_django_parity.py` compares them digit for digit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# Two places for anything in rupees, four for a quantity or a rate — a per-acre
# rate can carry paise, and an area like 65.7 sq km converts to a hectare figure
# that needs the room.
PAISE = Decimal("0.01")
QUANTITY = Decimal("0.0001")

#: Exact, by international definition: 1 acre = 4046.8564224 m2, 1 ha = 10000 m2.
#: Not an approximation, so it belongs here as a constant rather than a
#: magic number rounded differently in each caller.
HA_PER_ACRE = Decimal("0.40468564224")
HA_PER_SQ_KM = Decimal(100)


def money(value: Decimal | int | str) -> Decimal:
    """Round to paise, half away from zero. Every printed rupee goes through here."""
    return Decimal(value).quantize(PAISE, rounding=ROUND_HALF_UP)


def quantity(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(QUANTITY, rounding=ROUND_HALF_UP)


def to_hectares(qty: Decimal, unit: str) -> Decimal | None:
    """
    Convert a billed quantity to hectares, or None where the unit is not an area.

    🔴 CLAUDE.md: all area in hectares. This mirrors the generated column on
    `crm.invoice_line` so the API can show the conversion before the row is
    saved — the two must agree, and the smoke test asserts the database half.
    """
    match unit:
        case "acre":
            return quantity(qty * HA_PER_ACRE)
        case "sq_km":
            return quantity(qty * HA_PER_SQ_KM)
        case "hectare":
            return quantity(qty)
        case _:
            return None


@dataclass(frozen=True)
class LineAmounts:
    """What one invoice line contributes, already rounded to paise."""

    taxable: Decimal
    tax: Decimal
    total: Decimal


def compute_line(
    *,
    qty: Decimal,
    rate: Decimal,
    tax_rate_pct: Decimal,
    rate_is_tax_inclusive: bool = False,
    taxable_supply: bool = True,
) -> LineAmounts:
    """
    Amounts for a single line.

    ``rate_is_tax_inclusive`` is not a nicety — it is the difference between the
    two service lines this business actually sells. Spraying is quoted ex-tax at
    ₹150/acre with IGST added below the line. The Mizoram survey work is quoted
    at ₹32,000/sq km *including* GST, and the historical sheet records tax as
    zero against a total that already contains it. Treating the second like the
    first overstates revenue on every survey invoice by 18%.

    ``taxable_supply=False`` covers exempt, zero-rated and grant treatments:
    the gross is billed and no tax is separated out. 🔴 Which customers fall
    here is still open — see INVOICE.md §5.4. Nothing infers it.
    """
    gross = Decimal(qty) * Decimal(rate)

    if not taxable_supply:
        total = money(gross)
        return LineAmounts(taxable=total, tax=Decimal("0.00"), total=total)

    rate_pct = Decimal(tax_rate_pct)

    if rate_is_tax_inclusive:
        # Back out the tax the quoted rate already contains. Round the taxable
        # base first, then derive tax as the remainder, so the three numbers
        # reconcile exactly instead of being three independent roundings.
        total = money(gross)
        taxable = money(total * Decimal(100) / (Decimal(100) + rate_pct))
        return LineAmounts(taxable=taxable, tax=total - taxable, total=total)

    taxable = money(gross)
    tax = money(taxable * rate_pct / Decimal(100))
    return LineAmounts(taxable=taxable, tax=tax, total=taxable + tax)


def sum_lines(lines: list[LineAmounts]) -> LineAmounts:
    """Header totals. Plain addition of already-rounded lines — see rule 2."""
    return LineAmounts(
        taxable=sum((x.taxable for x in lines), Decimal("0.00")),
        tax=sum((x.tax for x in lines), Decimal("0.00")),
        total=sum((x.total for x in lines), Decimal("0.00")),
    )


# ---------------------------------------------------------------------------
# Amount in words
# ---------------------------------------------------------------------------

_ONES = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)


def _under_thousand(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")
    hundreds, rest = divmod(n, 100)
    out = f"{_ONES[hundreds]} hundred"
    return f"{out} {_under_thousand(rest)}" if rest else out


def _whole_in_words(n: int) -> str:
    """
    An integer in Indian numbering, lower case, with no currency and no "only".

    Kept separate from `rupees_in_words` so the crore branch can recurse
    without dragging the sentence furniture along with it — folding the two
    together produces "one hundred five only crore only".
    """
    if n == 0:
        return "zero"

    parts: list[str] = []
    crore, rest = divmod(n, 10_000_000)
    lakh, rest = divmod(rest, 100_000)
    thousand, rest = divmod(rest, 1_000)

    if crore:
        # Above 100 crore the unit repeats — "one hundred five crore" — so this
        # recurses rather than indexing a table of ever-larger scale names.
        parts.append(f"{_whole_in_words(crore)} crore")
    if lakh:
        parts.append(f"{_under_thousand(lakh)} lakh")
    if thousand:
        parts.append(f"{_under_thousand(thousand)} thousand")
    if rest:
        parts.append(_under_thousand(rest))
    return " ".join(parts)


def rupees_in_words(amount: Decimal, *, currency_prefix: str = "INR") -> str:
    """
    Indian numbering: crore, lakh, thousand, hundred.

    Not the short scale. ``6,45,519`` is "six lakh forty five thousand five
    hundred nineteen", never "six hundred forty five thousand". Getting this
    wrong is visible to every reader of the document, and one invoice in the
    historical set already reads "ninteen" because it was typed by hand.

    Paise are printed only when non-zero, which matches the existing documents.
    """
    amount = money(amount)
    negative = amount < 0
    amount = abs(amount)

    whole = int(amount)
    paise = int((amount - whole) * 100)

    words = _whole_in_words(whole).strip()
    out = f"{words[:1].upper()}{words[1:]}"

    if paise:
        p = _under_thousand(paise)
        out = f"{out} and {p} paise"

    out = f"{out} only"
    if currency_prefix:
        out = f"{currency_prefix} {out}"
    if negative:
        out = f"minus {out}"
    return out


def format_inr(amount: Decimal) -> str:
    """
    Indian digit grouping: ``2,50,96,77.00`` is wrong, ``25,09,677.00`` is right.

    The last three digits group together, then twos. Python's ``:,`` gives the
    western pattern, so this cannot be done with a format spec.
    """
    amount = money(amount)
    negative = amount < 0
    whole, _, frac = f"{abs(amount):.2f}".partition(".")

    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])

    return f"{'-' if negative else ''}{whole}.{frac}"
