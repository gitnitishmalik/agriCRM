"""
Users, roles and territories — the same `accounts_user` table Django owns.

🔴 `public_id`, not `id`, is what crosses into the business schemas. The DDL
types every user reference (`owner_user_id`, `created_by`, `changed_by`,
`crm.agent.user_id`) as `uuid` and carries no FK back here, so the business
schema never depends on the auth tables. The integer primary key exists for
Django's own machinery — django-otp, axes, the token blacklist — which still
runs during the migration.

That is why this module maps rather than redefines. Both services write the
same rows for as long as both are up.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db import Base

#: Doc 12 §1. Least to most privileged.
ROLES = (
    "field_agent",
    "bd_manager",
    "project_manager",
    "campaign_manager",
    "data_ops",
    "leadership",
    "compliance",
    "admin",
)

#: 🔴 Doc 12 §1 — MFA is mandatory for these, not offered. Kept identical to
#: `apps.accounts.models.MFA_REQUIRED_ROLES`; a test asserts the two agree,
#: because a role that is privileged in one service and not the other is a
#: hole that only appears once traffic is split between them.
MFA_REQUIRED_ROLES = frozenset({"data_ops", "campaign_manager", "compliance", "admin"})

#: Roles exempt from territory scoping in the RLS policy.
CROSS_TERRITORY_ROLES = frozenset({"data_ops", "compliance", "admin", "leadership"})


class User(Base):
    __tablename__ = "accounts_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)

    email: Mapped[str] = mapped_column(String(254), unique=True)
    password: Mapped[str] = mapped_column(String(128))
    full_name: Mapped[str] = mapped_column(String(200))
    phone_e164: Mapped[str] = mapped_column(String(16), default="")

    role: Mapped[str] = mapped_column(String(32), default="field_agent")
    district_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_enforced: Mapped[bool] = mapped_column(Boolean, default=False)

    date_joined: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_cross_territory(self) -> bool:
        return self.role in CROSS_TERRITORY_ROLES

    @property
    def requires_mfa(self) -> bool:
        """
        Derived from the role, not read from the column.

        `mfa_enforced` is maintained by Django's `save()`. Deriving it here
        rather than trusting the stored value means a row written by something
        that skipped that hook cannot leave a privileged account without a
        second factor.
        """
        return self.role in MFA_REQUIRED_ROLES

    def rls_context(self) -> dict[str, str]:
        """The three session variables the RLS policy reads (Doc 12 §3)."""
        return {
            "app.user_id": str(self.id),
            "app.user_role": self.role,
            "app.user_districts": ",".join(str(d) for d in (self.district_ids or [])),
        }


class TOTPDevice(Base):
    """
    django-otp's table, mapped so MFA verification works from either service.

    Not reimplemented: a user who enrolled an authenticator against Django must
    be able to sign in through FastAPI with the same app, and that only works
    if both read the same secret out of the same row.
    """

    __tablename__ = "otp_totp_totpdevice"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(64))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    key: Mapped[str] = mapped_column(String(80))
    step: Mapped[int] = mapped_column(Integer, default=30)
    t0: Mapped[int] = mapped_column(BigInteger, default=0)
    digits: Mapped[int] = mapped_column(Integer, default=6)
    tolerance: Mapped[int] = mapped_column(Integer, default=1)
    drift: Mapped[int] = mapped_column(Integer, default=0)
    last_t: Mapped[int] = mapped_column(BigInteger, default=-1)
    throttling_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    throttling_failure_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BlacklistedToken(Base):
    """
    simplejwt's blacklist, mapped so a logout in one service is a logout in
    the other. A refresh token revoked under Django must not still work here.
    """

    __tablename__ = "token_blacklist_blacklistedtoken"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    token_id: Mapped[int] = mapped_column(BigInteger)
    blacklisted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutstandingToken(Base):
    __tablename__ = "token_blacklist_outstandingtoken"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    jti: Mapped[str] = mapped_column(String(255), unique=True)
    token: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccessAttempt(Base):
    """django-axes' shared table, retained as FastAPI's durable lockout store."""

    __tablename__ = "axes_accessattempt"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    http_accept: Mapped[str] = mapped_column(String(1025), default="")
    path_info: Mapped[str] = mapped_column(String(255), default="/api/v1/auth/login/")
    attempt_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    get_data: Mapped[str] = mapped_column(Text, default="")
    post_data: Mapped[str] = mapped_column(Text, default="")
    failures_since_start: Mapped[int] = mapped_column(Integer, default=0)
