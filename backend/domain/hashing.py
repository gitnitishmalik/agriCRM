"""
Canonical hashing — the mechanism behind every "confirm exactly this" flow.

Four features in this module bind a human's confirmation to a specific set of
bytes: an AI proposal, a delivery preview, a reminder batch and a pre-issue
check run. All four have the same failure mode if the hash is sloppy — the user
approves what they were shown, something changes underneath, and the approval
silently carries over to different content.

🔴 So the canonical form matters more than the algorithm. `json.dumps` with
`sort_keys` alone is not enough: a `Decimal` raises, a `date` raises, and a
float would make `18.0` and `18` hash differently on different days depending
on how the value reached us. `canonical()` below flattens all of that into one
deterministic text form before it is hashed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _plain(value: Any) -> Any:
    """
    Reduce a value to something `json.dumps` can render deterministically.

    Money is the case that drives the design. A `Decimal("18.00")` and a
    `Decimal("18")` are equal numerically and differ as strings — and both
    reach here, because one came from the database and one from a request
    body. Normalising through `Decimal.normalize()` makes them one value, so a
    re-hash of unchanged data does not spuriously invalidate a confirmation.
    """
    if isinstance(value, Decimal):
        # `normalize()` alone gives 1.8E+1 for Decimal("18.00"); the format
        # spec pulls it back to plain notation.
        normalised = value.normalize()
        return (
            format(normalised, "f")
            if normalised == normalised.to_integral_value()
            else str(normalised)
        )
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        # 🔴 Not an accident that this is an error. A float in a payload that
        # is about to be hashed and confirmed is almost always money that lost
        # precision somewhere upstream, and hashing it would make the loss
        # permanent and invisible.
        raise TypeError(
            "Refusing to hash a float. Money and quantities are Decimal here; "
            "a float has already lost precision by the time it reaches this "
            "function, and hashing it would freeze that loss into a "
            "confirmation."
        )
    return str(value)


def canonical(payload: Any) -> str:
    """The one text form of a payload. Deterministic across processes."""
    return json.dumps(
        _plain(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def sha256_of(payload: Any) -> bytes:
    """Hash a structure. Raw bytes, because the DDL columns are `bytea`."""
    return hashlib.sha256(canonical(payload).encode("utf-8")).digest()


def sha256_bytes(content: bytes) -> bytes:
    """Hash raw bytes — a file, or a webhook body exactly as it arrived."""
    return hashlib.sha256(content).digest()


def hex_of(digest: bytes | None) -> str | None:
    """For display and for API responses. `bytea` is not JSON."""
    return digest.hex() if digest else None


def matches(digest: bytes | None, supplied: str | None) -> bool:
    """
    Compare a stored digest with one a client quoted back.

    Constant-time, and false for either side missing. A confirmation endpoint
    that treats a missing hash as "no need to check" is a confirmation
    endpoint with an opt-out, which is the same as not having one.
    """
    if not digest or not supplied:
        return False
    try:
        supplied_bytes = bytes.fromhex(supplied.strip())
    except ValueError:
        return False
    return hmac.compare_digest(digest, supplied_bytes)
