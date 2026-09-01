"""
Seed data: the two billing entities, and the development user roster.

🔴 **Every value in `BILLING_ENTITIES` was read off an actual issued invoice,
not invented.** A wrong GSTIN or account number on a generated document is
worse than no document: it is a document your customer will act on.

🔴 **Except the four that are not in this file, and must never be.** A bank
account number, a personal mobile and the names of the people who sign are
read from the environment, and the defaults here are obvious placeholders.

This repository is public. An account number plus a matching IFSC, sitting in
a file next to the company's real GSTIN and letterhead, is everything needed
to send a customer a convincing invoice pointing at a different account — and
redirected-payment fraud is the single most common way an invoicing system
hurts the business that runs it. The mobile and the signatory names are
personal data under DPDP besides.

The real values live in `.env`, which is gitignored, so seeding locally is
unchanged. `seed_billing_entities` says so out loud when it runs on the
placeholders, because an invoice rendered with `XXXXXXXXXXXX` on it should
never be a surprise.

TEPL appears twice, deliberately. Its bank moved from Axis to ICICI during
FY2026-27, and `crm.billing_entity` is versioned so re-rendering a 2025 invoice
prints the Axis block it was issued with. Two rows, adjacent date ranges, no
overlap — the exclusion constraint in the DDL enforces that.

Sources:
  TFD          — Invoice-2025/…/11- Invoice-Syngenta…2301+ 1346 Acres-UP Spray.pdf
  TEPL         — Invoice-2026/…/Formatt/Invoice-Sygenta-260 Acres-UP.docx
  TEPL (Axis)  — Invoice-2026/…/Axis Bank Invoice/ and the Mizoram set
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.accounts import User
from backend.models.billing import BillingEntity
from backend.security import hash_password

#: The date TEPL's printed bank block changes from Axis to ICICI. The ICICI
#: folder appears in the FY2026-27 set, so the switch is dated to the start of
#: that financial year until someone confirms the exact day.
TEPL_BANK_SWITCH = dt.date(2026, 4, 1)


#: 🔴 Not in the repository. See the module docstring.
#:
#: Read off `settings`, never `os.environ`. pydantic-settings loads `.env` onto
#: the settings object and never into the process environment, so the first
#: version of this — `os.environ.get("TFD_BANK_ACCOUNT_NO")` — never saw the
#: values a developer had actually configured. It seeded the placeholders in
#: silence, and the first sign was an issued-looking invoice carrying
#: `A/c No: XXXXXXXXXXXX`.
TFD_ACCOUNT_NO = settings.tfd_bank_account_no
TEPL_ACCOUNT_NO = settings.tepl_bank_account_no
BILLING_CONTACT_PHONE = settings.billing_contact_phone
TFD_CONTACT_NAME = settings.tfd_contact_name
TFD_SIGNATORY_NAME = settings.tfd_signatory_name

#: True when nothing was supplied, so the seeder can say so.
USING_PLACEHOLDER_BANK_DETAILS = "XXXXXXXXXXXX" in (TFD_ACCOUNT_NO, TEPL_ACCOUNT_NO)

BILLING_ENTITIES: list[dict[str, Any]] = [
    {
        "code": "TFD",
        "legal_name": "THETA FOUNDATION FOR DEVELOPMENT",
        "address_lines": [
            "L-20 Lower Basement, Front Portion",
            "Green Park, New Delhi - 110016",
        ],
        "state_code": "07",
        "gstin": "07AAICT8535C1Z9",
        "pan": "AAICT8535C",
        "contact_name": TFD_CONTACT_NAME,
        "contact_phone": BILLING_CONTACT_PHONE,
        "bank_name": "ICICI Bank",
        "bank_account_no": TFD_ACCOUNT_NO,
        "bank_ifsc": "ICIC0000029",
        "bank_branch": "Greater Kailash, New Delhi",
        "bank_address": "Greater Kailash Part-1, New Delhi - 110048",
        "signatory_name": TFD_SIGNATORY_NAME,
        "signatory_title": "Director",
        "template_code": "T1",
        "valid_from": dt.date(2024, 4, 1),
        "valid_to": None,
    },
    {
        # The historical version. Closed the day before the switch.
        "code": "TEPL",
        "legal_name": "THETA ENERLYTICS PRIVATE LIMITED",
        "address_lines": [
            "A 10/3 Front Ground Floor",
            "Vasant Vihar, New Delhi - 110057",
        ],
        "state_code": "07",
        "gstin": "07AAHCT0066D1ZM",
        "contact_phone": BILLING_CONTACT_PHONE,
        "bank_name": "AXIS Bank",
        "bank_branch": "New Delhi",
        "signatory_title": "Authorized Signatory",
        "declaration": (
            "We declare that this invoice shows the actual price of the goods "
            "described and that all particulars are true and correct."
        ),
        "jurisdiction_note": "Subject To New Delhi Jurisdiction",
        "template_code": "T2",
        "valid_from": dt.date(2024, 4, 1),
        "valid_to": TEPL_BANK_SWITCH - dt.timedelta(days=1),
    },
    {
        # Current.
        "code": "TEPL",
        "legal_name": "THETA ENERLYTICS PRIVATE LIMITED",
        "address_lines": [
            "A 10/3 Front Ground Floor",
            "Vasant Vihar, New Delhi - 110057",
        ],
        "state_code": "07",
        "gstin": "07AAHCT0066D1ZM",
        "contact_phone": BILLING_CONTACT_PHONE,
        "bank_name": "ICICI Bank Ltd.",
        "bank_account_no": TEPL_ACCOUNT_NO,
        "bank_ifsc": "ICIC0000719",
        "bank_branch": "E 222 East of Kailash, New Delhi - 110065",
        "signatory_title": "Authorized Signatory",
        "declaration": (
            "We declare that this invoice shows the actual price of the goods "
            "described and that all particulars are true and correct."
        ),
        "jurisdiction_note": "Subject To New Delhi Jurisdiction",
        "template_code": "T2",
        "valid_from": TEPL_BANK_SWITCH,
        "valid_to": None,
    },
]


async def seed_billing_entities(session: AsyncSession) -> list[BillingEntity]:
    """
    Create or refresh TFD and TEPL from the real invoices.

    🔴 Keyed on `(code, valid_from)`. That pair is what identifies a *version*,
    so re-running updates the row rather than opening a third overlapping one —
    which the DDL's exclusion constraint would refuse anyway, loudly and after
    the fact.
    """
    from datetime import UTC, datetime

    if USING_PLACEHOLDER_BANK_DETAILS:
        # Not an exception. Seeding is how a fresh checkout gets a working
        # database, and refusing would make the project unrunnable for anyone
        # who does not have the banking details — which is everyone outside
        # the company, and that is the intended state.
        for line in (
            "  ! Billing entities seeded with PLACEHOLDER bank details.",
            "    Invoices will render XXXXXXXXXXXX as the account number.",
            "    Set TFD_BANK_ACCOUNT_NO, TEPL_BANK_ACCOUNT_NO, BILLING_CONTACT_PHONE,",
            "    TFD_CONTACT_NAME and TFD_SIGNATORY_NAME in .env for the real ones.",
        ):
            print(line)

    rows: list[BillingEntity] = []

    for spec in BILLING_ENTITIES:
        entity = await session.scalar(
            select(BillingEntity).where(
                BillingEntity.code == spec["code"],
                BillingEntity.valid_from == spec["valid_from"],
            )
        )
        if entity is None:
            entity = BillingEntity(**spec, created_at=datetime.now(UTC))
            session.add(entity)
        else:
            for field, value in spec.items():
                setattr(entity, field, value)
        rows.append(entity)

    await session.flush()
    return rows


# ---------------------------------------------------------------------------
# Development users
# ---------------------------------------------------------------------------

#: Muzaffarnagar and Saharanpur — the UP cane belt, matching the districts the
#: smoke test seeds, so a developer's territory lines up with the sample data.
UP_CANE_DISTRICTS = [9001, 9002]

#: 🔴 A known password is the entire point of a dev seed — the alternative is
#: every developer inventing their own, which makes shared instructions
#: useless. What keeps it safe is the runtime guard in `seed_dev_users`, not
#: secrecy: it refuses to run unless DEBUG is on or SEED_DEV_USERS_ALLOWED=1.
DEFAULT_DEV_PASSWORD = "agricrm-dev-2026"

DEV_ACCOUNTS: list[dict[str, Any]] = [
    {
        "email": "agent@agricrm.local",
        "full_name": "Anil Sharma",
        "role": "field_agent",
        "district_ids": UP_CANE_DISTRICTS,
    },
    {
        "email": "agent.new@agricrm.local",
        "full_name": "Unassigned Agent",
        "role": "field_agent",
        "district_ids": [],
    },
    {
        "email": "ops@agricrm.local",
        "full_name": "Priya Nair",
        "role": "data_ops",
        "district_ids": [],
    },
    {
        "email": "campaigns@agricrm.local",
        "full_name": "Rahul Verma",
        "role": "campaign_manager",
        "district_ids": [],
    },
    {
        "email": "compliance@agricrm.local",
        "full_name": "Meera Iyer",
        "role": "compliance",
        "district_ids": [],
    },
    {
        "email": "leadership@agricrm.local",
        "full_name": "Sunil Kapoor",
        "role": "leadership",
        "district_ids": [],
    },
    {
        "email": "admin@agricrm.local",
        "full_name": "System Admin",
        "role": "admin",
        "district_ids": [],
    },
]


class SeedRefused(RuntimeError):
    """Raised when a dev seed is attempted outside development."""


async def seed_dev_users(session: AsyncSession, *, password: str | None = None) -> list[User]:
    """
    Create the development roster, one account per role.

    🔴 Refuses unless DEBUG is on, or SEED_DEV_USERS_ALLOWED=1 is set
    explicitly. These are known-password accounts; the guard is what stops one
    reaching an instance that holds real data (R11), and it is a runtime check
    rather than a comment because a comment does not stop anything.
    """
    from datetime import UTC, datetime

    from backend.config import settings
    from backend.models.accounts import MFA_REQUIRED_ROLES

    if not settings.debug and os.environ.get("SEED_DEV_USERS_ALLOWED") != "1":
        raise SeedRefused(
            "Refusing to seed known-password accounts: DEBUG is off and "
            "SEED_DEV_USERS_ALLOWED is not 1. 🔴 R11 — staging and production "
            "never hold these. Set the variable deliberately if this really is "
            "a development instance."
        )

    secret = password or DEFAULT_DEV_PASSWORD
    hashed = hash_password(secret)
    created: list[User] = []

    for spec in DEV_ACCOUNTS:
        user = await session.scalar(select(User).where(User.email == spec["email"]))
        if user is None:
            user = User(
                public_id=uuid.uuid4(),
                email=spec["email"],
                full_name=spec["full_name"],
                phone_e164="",
                password=hashed,
                role=spec["role"],
                district_ids=spec["district_ids"],
                is_active=True,
                is_staff=spec["role"] == "admin",
                is_superuser=spec["role"] == "admin",
                # Derived from the role, exactly as the API derives it — a
                # seeded account with the wrong MFA requirement would be a
                # privileged login without a second factor.
                mfa_enforced=spec["role"] in MFA_REQUIRED_ROLES,
                date_joined=datetime.now(UTC),
            )
            session.add(user)
        else:
            user.full_name = spec["full_name"]
            user.role = spec["role"]
            user.district_ids = spec["district_ids"]
            user.password = hashed
            user.is_active = True
            user.mfa_enforced = spec["role"] in MFA_REQUIRED_ROLES
        created.append(user)

    await session.flush()
    return created


async def totp_code(session: AsyncSession, email: str) -> str:
    """
    The current TOTP code for a user, for local sign-in without a phone.

    🔴 Development only, and it reads the secret that already exists rather
    than creating one — a helper that enrolled a device would be a way to give
    yourself a second factor you never possessed.
    """
    import pyotp

    from backend.models.accounts import TOTPDevice
    from backend.security import hex_key_to_base32

    user = await session.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        raise SeedRefused(f"No user {email}.")

    device = await session.scalar(select(TOTPDevice).where(TOTPDevice.user_id == user.id))
    if device is None:
        raise SeedRefused(
            f"{email} has no authenticator enrolled. Enrol one through "
            f"POST /api/v1/auth/mfa/enrol/ first."
        )

    return pyotp.TOTP(hex_key_to_base32(device.key), digits=device.digits).now()
