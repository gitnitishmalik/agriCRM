from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from backend.db import SessionLocal
from backend.models.accounts import User
from backend.security import hash_password, validate_new_password


async def create_admin(email: str, full_name: str) -> int:
    password = getpass.getpass("Password (12+ characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    errors = validate_new_password(password, email=email, full_name=full_name)
    if errors:
        raise SystemExit(" ".join(errors))
    async with SessionLocal() as session:
        if await session.scalar(select(User.id).where(User.email == email.lower())):
            raise SystemExit("A user with that email already exists.")
        session.add(
            User(
                public_id=uuid.uuid4(),
                email=email.lower(),
                full_name=full_name,
                phone_e164="",
                password=hash_password(password),
                role="admin",
                district_ids=[],
                is_active=True,
                is_staff=True,
                is_superuser=True,
                mfa_enforced=True,
                date_joined=datetime.now(UTC),
            )
        )
        await session.commit()
    print(f"Created FastAPI admin {email}. MFA will be required at sign-in.")
    return 0


async def seed_entities() -> int:
    """The two issuing companies, from the real invoice documents."""
    from backend.seeds import seed_billing_entities

    async with SessionLocal() as session:
        rows = await seed_billing_entities(session)
        await session.commit()

        print()
        print(f"{len(rows)} billing entity versions ready.")
        print()
        for entity in rows:
            window = f"{entity.valid_from} to {entity.valid_to or 'current'}"
            print(
                f"  {entity.code:<5} {entity.gstin or '-':<18} "
                f"{entity.bank_name or '-':<16} {window}"
            )
        print()
        print(
            "  !! Do not edit these in place. Changing a bank, address or "
            "signatory means closing the current row and opening a new one, "
            "so an old invoice still re-renders with the details it was "
            "issued under."
        )
        print()
    return 0


async def seed_users(password: str | None) -> int:
    """The development roster, one account per role."""
    from backend.seeds import DEFAULT_DEV_PASSWORD, SeedRefused, seed_dev_users

    async with SessionLocal() as session:
        try:
            users = await seed_dev_users(session, password=password)
        except SeedRefused as error:
            print(error)
            return 1
        await session.commit()

        print()
        print(f"{len(users)} development accounts ready.")
        print(f"Password: {password or DEFAULT_DEV_PASSWORD}")
        print()
        for user in users:
            factor = "MFA required" if user.mfa_enforced else "password only"
            print(f"  {user.email:<30} {user.role:<18} {factor}")
        print()
    return 0


async def show_totp(email: str) -> int:
    """Print the current code for a user whose authenticator is enrolled."""
    from backend.seeds import SeedRefused, totp_code

    async with SessionLocal() as session:
        try:
            print(await totp_code(session, email))
        except SeedRefused as error:
            print(error)
            return 1
    return 0


def _use_utf8_output() -> None:
    """
    🔴 Make the console able to print the messages this CLI actually emits.

    Windows consoles default to cp1252, which cannot encode 🔴 (U+1F534) — and
    that character appears in exactly the messages that matter most, including
    the refusal that stops known-password accounts being seeded onto an
    instance holding real data. Without this, that safety refusal came out as
    a `UnicodeEncodeError` traceback: the guard worked, and the operator saw a
    crash instead of the reason.

    `errors="replace"` rather than a hard switch, because a console that still
    cannot render a glyph should drop the glyph, not the sentence.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                # A redirected or exotic stream. Not worth failing over.
                pass


def main() -> int:
    _use_utf8_output()
    parser = argparse.ArgumentParser(description="AgriCRM administration")
    sub = parser.add_subparsers(dest="command", required=True)

    admin = sub.add_parser("create-admin", help="Create an administrator account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)

    sub.add_parser(
        "seed-billing-entities",
        help="Create or refresh TFD and TEPL from the real invoice documents",
    )

    users = sub.add_parser(
        "seed-dev-users", help="Development roster (refuses outside development)"
    )
    users.add_argument("--password", default=None)

    totp = sub.add_parser("totp-code", help="Current TOTP code for a user (dev)")
    totp.add_argument("--email", required=True)

    args = parser.parse_args()

    if args.command == "create-admin":
        return asyncio.run(create_admin(args.email, args.name))
    if args.command == "seed-billing-entities":
        return asyncio.run(seed_entities())
    if args.command == "seed-dev-users":
        return asyncio.run(seed_users(args.password))
    if args.command == "totp-code":
        return asyncio.run(show_totp(args.email))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
