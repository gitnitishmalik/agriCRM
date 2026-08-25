"""
Users, roles and territories.

🔴 Swapping AUTH_USER_MODEL after the first migration is a painful, error-prone
operation. This model is defined in full in Phase 0 precisely so it never has
to change shape later.

The role and territory fields are not decoration — they feed the PostgreSQL
Row-Level Security session variables (Doc 12 §3). RLS is the backstop that
makes an application bug unable to leak another region's data, and it reads
`app.user_id`, `app.user_role` and `app.user_districts` from the connection.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    """Doc 12 §1. Order is least to most privileged."""

    FIELD_AGENT = "field_agent", "Field / BD Agent"
    BD_MANAGER = "bd_manager", "BD Manager"
    PROJECT_MANAGER = "project_manager", "Project Manager"
    CAMPAIGN_MANAGER = "campaign_manager", "Campaign Manager"
    DATA_OPS = "data_ops", "Data Ops Analyst"
    LEADERSHIP = "leadership", "Leadership"
    COMPLIANCE = "compliance", "Compliance Officer"
    ADMIN = "admin", "System Admin"


#: 🔴 Doc 12 §1 — MFA is mandatory for these roles, not offered.
MFA_REQUIRED_ROLES = frozenset({Role.DATA_OPS, Role.CAMPAIGN_MANAGER, Role.COMPLIANCE, Role.ADMIN})

#: Roles exempt from territory scoping in the RLS policy. Kept here so the
#: policy SQL and the application agree on one list rather than two.
CROSS_TERRITORY_ROLES = frozenset({Role.DATA_OPS, Role.COMPLIANCE, Role.ADMIN, Role.LEADERSHIP})


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra):
        if not email:
            raise ValueError("Users must have an email address")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("role", Role.ADMIN)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=200)
    phone_e164 = models.CharField(max_length=16, blank=True)

    role = models.CharField(
        max_length=32,
        choices=Role.choices,
        default=Role.FIELD_AGENT,
        help_text="Primary role. Drives RLS scoping and the MFA requirement.",
    )

    # Territory. LGD district codes, matching ref.district.id.
    # Empty means no territory — which for a non-cross-territory role means
    # the user sees nothing, deliberately. Failing closed is the right default.
    district_ids = ArrayField(
        models.IntegerField(),
        default=list,
        blank=True,
        help_text="LGD district codes this user may access.",
    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    mfa_enforced = models.BooleanField(
        default=False,
        help_text="Set automatically for roles in MFA_REQUIRED_ROLES.",
    )
    date_joined = models.DateTimeField(default=timezone.now)

    # Doc 12 §14: offboarding revokes within 24h. Recording the intent here
    # lets a scheduled job assert it actually happened.
    deactivated_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        db_table = "accounts_user"
        indexes = [models.Index(fields=["role"])]

    def __str__(self) -> str:
        return f"{self.full_name} <{self.email}>"

    def save(self, *args, **kwargs):
        # Derive rather than trust: an admin editing a role must not be able to
        # leave MFA off for a role that requires it.
        self.mfa_enforced = self.role in MFA_REQUIRED_ROLES
        super().save(*args, **kwargs)

    # -- RLS session context (Doc 12 §3) ----------------------------------

    @property
    def is_cross_territory(self) -> bool:
        return self.role in CROSS_TERRITORY_ROLES

    @property
    def district_csv(self) -> str:
        """Rendered for `app.user_districts`; the policy casts it to int[]."""
        return ",".join(str(d) for d in self.district_ids)

    def rls_context(self) -> dict[str, str]:
        return {
            "app.user_id": str(self.pk),
            "app.user_role": self.role,
            "app.user_districts": self.district_csv,
        }
