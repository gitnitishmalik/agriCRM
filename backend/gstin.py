"""
GSTIN validation and place-of-supply derivation.

🔴 This module exists because of a real defect. 29 of 105 lines in the
historical invoice sheet carry a GSTIN one character short, and the same
customer appears both ways — Syngenta UP as ``09AAECS9424P1ZL`` on 16 lines and
``09AAECS942P1ZL`` on 15. A wrong GSTIN on a filed invoice blocks the
customer's input tax credit and surfaces as a GSTR-2B mismatch on their side,
so it is their problem before it is yours, which is the worst kind.

The structural fix is that a GSTIN is entered once against
``core.organisation`` and never retyped onto an invoice. This module is the
second line: it refuses a malformed one at the point of entry.
"""

from __future__ import annotations

import re

#: 2-digit state code, 10-character PAN, 1 entity digit, 'Z', 1 check character.
#: The PAN block is itself structured: 5 letters, 4 digits, 1 letter.
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

#: Government departments are issued a UIN that does not follow the PAN
#: pattern — Mizoram's Department of Agriculture bills under
#: ``15SHLD02015GIDQ``. Allowing it needs an explicit flag on the record rather
#: than a weaker regex, or the flag stops meaning anything and the validator
#: stops catching the typos it exists to catch.
GOVT_UIN_RE = re.compile(r"^[0-9]{2}[A-Z0-9]{13}$")

#: The check-digit alphabet, in order: value 0-9 then A-Z.
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: GST state codes. Keyed the way they are printed on the document.
STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
    "99": "Centre Jurisdiction",
}


class GSTINError(ValueError):
    """Raised with a message written for the person typing, not for a log."""


def check_digit(first_fourteen: str) -> str:
    """
    The 15th character, per the GSTN algorithm.

    Each of the first 14 characters takes its value from ``_ALPHABET``,
    multiplied by 1 or 2 alternating from the left. Products are reduced by
    dividing by 36 and adding quotient to remainder, summed, and the check
    character is whatever brings that sum to a multiple of 36.
    """
    total = 0
    for i, ch in enumerate(first_fourteen):
        value = _ALPHABET.index(ch)
        product = value * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def validate(value: str, *, allow_govt_uin: bool = False) -> str:
    """
    Return the normalised GSTIN, or raise ``GSTINError`` saying what is wrong.

    Messages name the actual problem. "Invalid GSTIN" tells the person typing
    nothing they can act on; "15 characters expected, this has 14" tells them
    they dropped a digit, which is exactly the defect in the historical data.
    """
    if value is None:
        raise GSTINError("GSTIN is required.")

    cleaned = re.sub(r"[\s-]", "", str(value)).upper()

    if not cleaned:
        raise GSTINError("GSTIN is required.")

    if len(cleaned) != 15:
        raise GSTINError(
            f"A GSTIN is 15 characters; this has {len(cleaned)}. "
            f"Check for a missing digit in the PAN block or a dropped leading "
            f"state code."
        )

    state = cleaned[:2]
    if state not in STATE_CODES:
        raise GSTINError(f"'{state}' is not a GST state code.")

    if GSTIN_RE.match(cleaned):
        expected = check_digit(cleaned[:14])
        if cleaned[14] != expected:
            raise GSTINError(
                f"The check character does not match: this GSTIN ends '{cleaned[14]}' "
                f"but its first 14 characters compute to '{expected}'. "
                f"Usually a mistyped character earlier in the number."
            )
        return cleaned

    if allow_govt_uin and GOVT_UIN_RE.match(cleaned):
        # A government UIN carries no PAN and no check digit to verify.
        return cleaned

    raise GSTINError(
        "This is 15 characters but not in GSTIN form "
        "(2-digit state, 10-character PAN, entity digit, 'Z', check character). "
        "If this is a government department UIN, tick 'government UIN'."
    )


def is_valid(value: str, *, allow_govt_uin: bool = False) -> bool:
    try:
        validate(value, allow_govt_uin=allow_govt_uin)
    except GSTINError:
        return False
    return True


def state_code(gstin: str) -> str | None:
    cleaned = re.sub(r"[\s-]", "", str(gstin or "")).upper()
    return cleaned[:2] if len(cleaned) >= 2 and cleaned[:2] in STATE_CODES else None


def state_name(code: str) -> str | None:
    return STATE_CODES.get(code)


def derive_tax_treatment(supplier_state: str, buyer_state: str | None) -> str:
    """
    Suggest IGST or CGST+SGST from the two state codes.

    For a service supplied to a registered person, place of supply is the
    recipient's location — so a buyer outside the supplier's state is
    inter-state and takes IGST. Both billing entities are Delhi (07), and every
    customer in the historical data is elsewhere, which is why every invoice
    shows IGST.

    🔴 This is a *suggestion*. The caller shows it and lets a human override,
    because the historical data has mill invoices with no tax at all and
    INVOICE.md §5.4 is still waiting on the CA to say whether those are exempt
    supplies or grant disbursements. Nothing here decides that.
    """
    if not buyer_state:
        return "igst"
    return "cgst_sgst" if buyer_state == supplier_state else "igst"
