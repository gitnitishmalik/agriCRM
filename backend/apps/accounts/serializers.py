"""Auth serializers (Doc 11 §2)."""

from __future__ import annotations

from django.contrib.auth.password_validation import validate_password
from django_otp import user_has_device
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import MFA_REQUIRED_ROLES, User


class UserSerializer(serializers.ModelSerializer):
    """`GET /auth/me/` — identity, role, permissions and territory."""

    permissions = serializers.SerializerMethodField()
    is_cross_territory = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "full_name",
            "role",
            "district_ids",
            "is_cross_territory",
            "mfa_enforced",
            "permissions",
        )
        read_only_fields = fields

    def get_permissions(self, obj: User) -> list[str]:
        return sorted(obj.get_all_permissions())


class LoginSerializer(TokenObtainPairSerializer):
    """
    Issues the token pair and reports whether MFA still has to be satisfied.

    🔴 Doc 12 §1: MFA is mandatory for data_ops, campaign_manager, compliance
    and admin. The token is issued either way, but `mfa_satisfied: false` means
    the client must complete /auth/mfa/verify/ before the token is accepted by
    any protected endpoint — enforced by IsMFAVerified, not by client goodwill.
    """

    @classmethod
    def get_token(cls, user: User):
        token = super().get_token(user)
        token["role"] = user.role
        token["mfa_required"] = user.role in MFA_REQUIRED_ROLES
        token["mfa_satisfied"] = user.role not in MFA_REQUIRED_ROLES
        return token

    def validate(self, attrs: dict) -> dict:
        data = super().validate(attrs)
        user: User = self.user
        data["user"] = UserSerializer(user).data
        data["mfa_required"] = user.role in MFA_REQUIRED_ROLES
        data["mfa_enrolled"] = user_has_device(user, confirmed=True)
        return data


class MFAVerifySerializer(serializers.Serializer):
    token = serializers.CharField(min_length=6, max_length=8)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value: str) -> str:
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value: str) -> str:
        validate_password(value, self.context["request"].user)
        return value


# ---------------------------------------------------------------------------
# Response shapes
#
# Declared explicitly so drf-spectacular emits a typed contract rather than a
# bare `{}`. Doc 03 §4 generates the frontend's TypeScript client from this
# schema — an untyped response here becomes an `any` in the React app, which
# is exactly the class of bug TypeScript was adopted to prevent.
# ---------------------------------------------------------------------------


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class MFAEnrolResponseSerializer(serializers.Serializer):
    provisioning_uri = serializers.CharField(
        help_text="otpauth:// URI. Contains the shared secret — never log it."
    )
    qr_svg = serializers.CharField(help_text="Inline SVG QR code for the URI above.")
    secret = serializers.CharField(help_text="Base32 secret, for manual entry.")
    already_confirmed = serializers.BooleanField(
        help_text="True if this device was already enrolled and confirmed."
    )


class LogoutRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
