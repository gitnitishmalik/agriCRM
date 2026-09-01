"""
Tokens, passwords and the second factor.

🔴 The token and password formats are inherited from the retired Django
service and must not be "modernised" casually. They were kept wire-compatible
during the migration so a token minted by either service verified in the
other; Django is gone, but the formats now have real rows behind them:

  * HS256 over `settings.secret_key`, with the claim names simplejwt used —
    `token_type`, `exp`, `iat`, `jti`, `user_id` — plus the three this project
    adds: `role`, `mfa_required`, `mfa_satisfied`. Changing a claim name
    invalidates every unexpired refresh token, which presents as every user
    being logged out at once.
  * Django's PBKDF2 password format, because **every existing password hash is
    in it**. Switching algorithms means either a re-hash-on-next-login path or
    a forced password reset for every user — a migration, not an edit.

The MFA claim rules are carried over from the Phase 1 work rather than
re-derived. They were built against a real hole and they are the reason this
service is not a step backwards.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from jose import JWTError, jwt

from backend.config import settings

ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# Passwords — Django's format, so existing users can sign in
# ---------------------------------------------------------------------------


def verify_password(raw: str, encoded: str) -> bool:
    """
    Check a password against Django's `pbkdf2_sha256$iterations$salt$hash`.

    Reimplemented rather than delegated to passlib because the format is
    small, exactly specified, and the alternative is a dependency whose
    default iteration count could drift from Django's and silently start
    rejecting valid passwords.

    Unknown or unusable hashes return False rather than raising: the bypass
    user is created with an unusable password precisely so it can never be a
    way in, and that has to stay true here.
    """
    if not encoded or "$" not in encoded:
        return False

    algorithm, _, rest = encoded.partition("$")
    if algorithm != "pbkdf2_sha256":
        return False

    try:
        iterations_text, salt, expected = rest.split("$", 2)
        iterations = int(iterations_text)
    except (ValueError, TypeError):
        return False

    derived = hashlib.pbkdf2_hmac("sha256", raw.encode(), salt.encode(), iterations)
    # 🔴 Constant time. A short-circuiting comparison leaks how much of the
    # hash matched, which is enough to recover it one byte at a time.
    return hmac.compare_digest(base64.b64encode(derived).decode(), expected)


def hash_password(raw: str, iterations: int = 1_000_000) -> str:
    """Produce a hash Django can also verify."""
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", raw.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${base64.b64encode(derived).decode()}"


COMMON_PASSWORDS = frozenset(
    {
        "123456789012",
        "1234567890",
        "password1234",
        "password123",
        "qwertyuiop12",
        "qwerty123456",
        "administrator",
        "letmein12345",
        "welcome12345",
        "iloveyou1234",
    }
)


def validate_new_password(raw: str, *, email: str = "", full_name: str = "") -> list[str]:
    """Password policy without importing Django at runtime."""
    errors: list[str] = []
    lowered = raw.casefold()
    if len(raw) < 12:
        errors.append("Password must contain at least 12 characters.")
    if lowered in COMMON_PASSWORDS or lowered.isdigit():
        errors.append("Password is too common or entirely numeric.")

    attributes = [email.partition("@")[0], full_name]
    for value in attributes:
        value = value.strip().casefold()
        if len(value) >= 4 and (
            value in lowered or SequenceMatcher(a=lowered, b=value).quick_ratio() >= 0.7
        ):
            errors.append("Password is too similar to your personal information.")
            break
    return errors


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def _claims(user_id: int, token_type: str, lifetime: timedelta, extra: dict[str, Any]) -> dict:
    now = datetime.now(UTC)
    return {
        "token_type": token_type,
        "exp": now + lifetime,
        "iat": now,
        "jti": uuid.uuid4().hex,
        "user_id": user_id,
        **extra,
    }


def issue_pair(
    user_id: int, *, role: str, mfa_required: bool, mfa_satisfied: bool
) -> dict[str, str]:
    """
    An access/refresh pair carrying this project's three extra claims.

    🔴 `mfa_satisfied` is false for a privileged role until the second factor
    is verified. The token is issued either way — it has to be, or the user
    could never reach the enrolment endpoint — and it opens nothing.
    """
    extra = {"role": role, "mfa_required": mfa_required, "mfa_satisfied": mfa_satisfied}

    access = _claims(user_id, "access", timedelta(minutes=settings.access_token_minutes), extra)
    refresh = _claims(user_id, "refresh", timedelta(days=settings.refresh_token_days), extra)

    return {
        "access": jwt.encode(access, settings.secret_key, algorithm=ALGORITHM),
        "refresh": jwt.encode(refresh, settings.secret_key, algorithm=ALGORITHM),
    }


def decode(token: str, *, expected_type: str = "access") -> dict[str, Any]:
    """
    Verify and decode. Raises `JWTError` on anything wrong.

    🔴 `user_id` is normalised to an int.

    django-rest-framework-simplejwt serialises the primary key as a *string* —
    `"user_id": "1"` — while this service writes an integer. Both are valid
    JWTs and both verify, so the mismatch does not surface as an
    authentication failure: it surfaces as `SELECT ... WHERE id = '1'` finding
    nobody, and a user who signed in through Django getting a 401 from
    FastAPI that says "no such active user" about an account that plainly
    exists.

    Normalising on read rather than matching simplejwt on write, because this
    service should keep emitting the correct type and still accept theirs.
    """
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    if payload.get("token_type") != expected_type:
        raise JWTError(f"expected a {expected_type} token, got {payload.get('token_type')!r}")

    if "user_id" in payload:
        try:
            payload["user_id"] = int(payload["user_id"])
        except (TypeError, ValueError) as error:
            raise JWTError("user_id claim is not an integer") from error

    return payload


def refresh_pair(refresh_token: str) -> tuple[dict[str, str], dict[str, Any]]:
    """
    Trade a refresh token for a new pair.

    🔴 The MFA claims are *copied forward*, never granted. Minting them fresh
    here would make refresh the way around the second factor: sign in, ignore
    the MFA screen, refresh once, and you hold a satisfied token having proved
    one factor. The Phase 1 suite has a test for exactly that sequence.
    """
    payload = decode(refresh_token, expected_type="refresh")

    pair = issue_pair(
        payload["user_id"],
        role=payload.get("role", "field_agent"),
        mfa_required=bool(payload.get("mfa_required")),
        mfa_satisfied=bool(payload.get("mfa_satisfied")),
    )
    return pair, payload


# ---------------------------------------------------------------------------
# The second factor
# ---------------------------------------------------------------------------


def mfa_is_satisfied(claims: dict[str, Any], *, user_requires_mfa: bool) -> bool:
    """
    🔴 The Phase 1 rule, carried over unchanged.

    Both claims are required, not just `mfa_satisfied`. A token minted for a
    field agent carries `mfa_satisfied: true` truthfully, because MFA did not
    apply to the role it was issued for. Promote that user to data_ops and the
    claim is still sitting in their browser — a privileged session that never
    proved a second factor, for as long as they keep refreshing.

    `mfa_required` is only true on a token issued *knowing* MFA applied, so
    demanding both refuses the pre-promotion token and sends the user back
    through sign-in under the role they now hold.
    """
    if not settings.require_mfa:
        return True
    if not user_requires_mfa:
        return True
    return bool(claims.get("mfa_required")) and bool(claims.get("mfa_satisfied"))


def verify_totp(secret_b32: str, code: str, *, step: int = 30, digits: int = 6) -> bool:
    """
    Check a TOTP code against the secret stored by django-otp.

    django-otp keeps the key as hex; pyotp wants base32. The conversion is in
    `api/routers/auth.py` where the row is read, so this stays a pure function
    that a test can drive without a database.
    """
    import pyotp

    totp = pyotp.TOTP(secret_b32, interval=step, digits=digits)
    # valid_window=1 accepts the previous and next step, which is what makes a
    # code typed by a person on a phone with imperfect clock sync work.
    return totp.verify(code, valid_window=1)


def verify_totp_counter(
    secret_b32: str,
    code: str,
    *,
    step: int = 30,
    digits: int = 6,
    tolerance: int = 1,
    drift: int = 0,
    last_t: int = -1,
) -> tuple[bool, int, int]:
    """Verify once and return ``(valid, counter, new_drift)``.

    django-otp records the last accepted counter. Merely checking the six
    digits lets the same code be replayed until its 30-second window closes.
    Returning the matched counter lets the route persist the same replay
    protection in the shared ``otp_totp_totpdevice`` row.
    """
    import pyotp

    if not code.isdigit() or len(code) != digits:
        return False, last_t, drift

    totp = pyotp.TOTP(secret_b32, interval=step, digits=digits)
    current = int(time.time()) // step
    for offset in range(-tolerance, tolerance + 1):
        counter = current + drift + offset
        if counter <= last_t:
            continue
        expected = totp.at(counter * step)
        if hmac.compare_digest(expected, code):
            return True, counter, counter - current
    return False, last_t, drift


def hex_key_to_base32(hex_key: str) -> str:
    """django-otp stores the shared secret as hex; authenticator apps use base32."""
    return base64.b32encode(bytes.fromhex(hex_key)).decode().rstrip("=")


def now_timestamp() -> int:
    return int(time.time())
