"""
🔴 Existing users must still be able to sign in.

Django is gone from this repository. The password hashes it wrote are not —
they sit in `accounts_user.password` and nothing rewrites them, because a
hash cannot be recomputed without the plaintext. A user's password is
re-hashed in this service's format only when they next change it, which for
most people is never.

So "can we verify Django's PBKDF2 format" is not a migration-era question that
retired with the framework. It is a permanent property of this service, and
the day it stops being true every existing account is locked out at once.

**The hashes below are real, captured from Django 5.2.17 before it was
removed.** Pinning them is deliberate and is stronger than the test that
replaced it: the old version imported Django and hashed a password at test
time, which only ever proved the service agreed with *whichever Django
happened to be installed*. These are the bytes that are actually in the
database.
"""

from __future__ import annotations

import pytest

from backend.security import hash_password, validate_new_password, verify_password

pytestmark = pytest.mark.anyio

#: Produced by `django.contrib.auth.hashers.make_password` on Django 5.2.17,
#: the version this project ran before the port. Format:
#: `pbkdf2_sha256$<iterations>$<salt>$<base64 hash>`.
DJANGO_HASHES: dict[str, str] = {
    "correct-horse-battery-staple": (
        "pbkdf2_sha256$1000000$ai9FSXgSRyxgqoxNPp68p2$2xP0sfQolc1qibFarChew7nRJEMnhdQ6bO1oS0qu5/Q="
    ),
    "agricrm-dev-2026": (
        "pbkdf2_sha256$1000000$bUvTBSk1uriXGADU1CPutv$u8hzhle26y3f41QVo7Favb3VqLGIHz8w1KmUjDhBoTM="
    ),
}

#: Django 5.2's PBKDF2 work factor. 🔴 A *floor*, not a target: lowering it
#: would silently weaken every password set from here on, and nothing else in
#: the system would notice.
DJANGO_ITERATIONS = 1_000_000


async def test_a_django_written_password_still_verifies():
    """
    🔴 The one that locks everybody out if it breaks.

    These hashes are in the database right now. No migration rewrites them.
    """
    for plaintext, encoded in DJANGO_HASHES.items():
        assert verify_password(plaintext, encoded), (
            f"a Django-written hash for {plaintext!r} no longer verifies — "
            f"every existing user is locked out"
        )
        assert not verify_password("the-wrong-password", encoded)


async def test_a_hash_written_here_has_the_same_shape():
    """
    Same algorithm, same work factor, same encoding. A password changed
    through this service has to be indistinguishable in the column from one
    Django wrote — otherwise the two formats diverge and something eventually
    has to know which is which.
    """
    encoded = hash_password("a-long-and-unique-test-password")
    algorithm, iterations, salt, digest = encoded.split("$", 3)

    assert algorithm == "pbkdf2_sha256"
    assert int(iterations) >= DJANGO_ITERATIONS, (
        f"work factor dropped to {iterations} from {DJANGO_ITERATIONS} — that "
        f"weakens every password set from here on, silently"
    )
    assert salt and digest
    assert verify_password("a-long-and-unique-test-password", encoded)
    assert not verify_password("nearly-the-right-password", encoded)


async def test_an_unusable_password_is_refused():
    """
    🔴 The dev bypass user is created with an unusable password precisely so
    that row can never become a way in once the bypass is switched off.

    Django spells "no password" as a value starting `!`. Anything that reaches
    `verify_password` in that shape must fail closed.
    """
    for encoded in ("!", "", "!abc123", "!" + DJANGO_HASHES["agricrm-dev-2026"]):
        assert not verify_password("anything", encoded)
        assert not verify_password("", encoded)


async def test_a_malformed_hash_fails_closed():
    """
    A truncated or corrupted column must be a refusal, not an exception — an
    unhandled error on the login path is a different kind of outage.
    """
    for encoded in (
        "pbkdf2_sha256$1000000$onlythreeparts",
        "pbkdf2_sha256$notanumber$salt$digest",
        "bcrypt$12$something",
        "$$$$",
    ):
        assert not verify_password("anything", encoded)


async def test_the_password_policy_still_refuses_the_obvious():
    """
    Carried over from Django's validators. The rules are the project's, not
    the framework's, so they outlive it.
    """
    assert validate_new_password("short")
    assert validate_new_password("password123")
    assert validate_new_password("123456789012")
    assert validate_new_password("priya.nair@agricrm.local", email="priya.nair@agricrm.local")
    # And a good one passes.
    assert validate_new_password("kingfisher-monsoon-lattice-9") == []
