"""
Create the standard set of development accounts.

🔴 Refuses to run unless DEBUG is on, or SEED_DEV_USERS_ALLOWED=1 is set
explicitly. These are known-password accounts; the guard is what stops one
reaching an environment where someone can log into it from the internet.

The set is chosen to cover the access paths that behave differently, so a
developer can check each one without hand-building users:

  * a field agent scoped to two districts   — territory scoping, masked PII
  * a field agent with no territory         — the fails-closed case
  * a data-ops analyst                      — 🔴 MFA-required path
  * a campaign manager                      — MFA, and cannot see phone numbers
  * a compliance officer                    — MFA, read-only over audit data
  * leadership                              — cross-territory, read-mostly
  * an admin                                — Django Admin access

Run it repeatedly; it updates rather than duplicates.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Role, User

# Muzaffarnagar and Saharanpur — the UP cane belt, matching the districts the
# smoke test seeds so a developer's territory lines up with the sample data.
UP_CANE_DISTRICTS = [9001, 9002]

# A known password is the entire point of a dev seed — the alternative is
# every developer inventing their own, which makes shared instructions useless.
# What keeps it safe is the runtime guard in handle(), not secrecy: the command
# refuses to run unless DEBUG is on or SEED_DEV_USERS_ALLOWED=1 is set.
DEFAULT_PASSWORD = "agricrm-dev-2026"  # noqa: S105

ACCOUNTS: list[dict] = [
    {
        "email": "agent@agricrm.local",
        "full_name": "Anil Sharma",
        "role": Role.FIELD_AGENT,
        "district_ids": UP_CANE_DISTRICTS,
        "note": "Territory-scoped. Sees only districts 9001, 9002.",
    },
    {
        "email": "agent.new@agricrm.local",
        "full_name": "Unassigned Agent",
        "role": Role.FIELD_AGENT,
        "district_ids": [],
        "note": "No territory - sees nothing. The fails-closed case.",
    },
    {
        "email": "ops@agricrm.local",
        "full_name": "Priya Nair",
        "role": Role.DATA_OPS,
        "district_ids": [],
        "note": "Cross-territory. Import, merge and quarantine rights.",
    },
    {
        "email": "campaigns@agricrm.local",
        "full_name": "Rahul Verma",
        "role": Role.CAMPAIGN_MANAGER,
        "district_ids": [],
        "note": "Builds segments and campaigns; cannot see phone numbers.",
    },
    {
        "email": "compliance@agricrm.local",
        "full_name": "Meera Iyer",
        "role": Role.COMPLIANCE,
        "district_ids": [],
        "note": "Audits consent and access logs; cannot edit business data.",
    },
    {
        "email": "leadership@agricrm.local",
        "full_name": "Sunil Kapoor",
        "role": Role.LEADERSHIP,
        "district_ids": [],
        "note": "Cross-territory, read-mostly. Contacts stay masked.",
    },
    {
        "email": "admin@agricrm.local",
        "full_name": "System Admin",
        "role": Role.ADMIN,
        "district_ids": [],
        "note": "Full access. Django Admin at /admin/.",
        "staff": True,
    },
]


class Command(BaseCommand):
    help = "Create or refresh the standard development accounts. Dev only."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help=f"Password for every seeded account (default: {DEFAULT_PASSWORD})",
        )

    def handle(self, *args, **options):
        allowed = settings.DEBUG or __import__("os").environ.get("SEED_DEV_USERS_ALLOWED") == "1"
        if not allowed:
            raise CommandError(
                "Refusing to run: DEBUG is off and SEED_DEV_USERS_ALLOWED is not set.\n"
                "These accounts have a known password. Creating them outside "
                "development would put a publicly-guessable login on a live system."
            )

        password: str = options["password"]

        with transaction.atomic():
            rows = [self._upsert(spec, password) for spec in ACCOUNTS]

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{len(rows)} development accounts ready."))
        self.stdout.write("")
        self.stdout.write(f"  Password for all of them:  {password}")
        self.stdout.write("")

        width = max(len(r["email"]) for r in rows)
        for row in rows:
            flag = "MFA" if row["mfa"] else "   "
            self.stdout.write(f"  {row['email']:<{width}}  {flag}  {row['note']}")

        self.stdout.write("")
        self.stdout.write(
            "  Accounts marked MFA stop at a second step. Sign in, then use\n"
            "  'Set up an authenticator app' on that screen to enrol."
        )
        self.stdout.write("")

    def _upsert(self, spec: dict, password: str) -> dict:
        user, _ = User.objects.get_or_create(
            email=spec["email"], defaults={"full_name": spec["full_name"]}
        )
        user.full_name = spec["full_name"]
        user.role = spec["role"]
        user.district_ids = spec["district_ids"]
        user.is_active = True
        if spec.get("staff"):
            user.is_staff = True
            user.is_superuser = True
        user.set_password(password)
        user.save()  # save() derives mfa_enforced from role

        return {"email": user.email, "note": spec["note"], "mfa": user.mfa_enforced}
