"""
Phase 0 exit-gate tests.

These assert the foundation behaves, not that features exist. Anything marked
`compliance` encodes a rule from Doc 05 §5 and must never be skipped or
weakened to make a build pass.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import MFA_REQUIRED_ROLES, Role, User
from config.logging_filters import scrub, scrub_mapping

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def agent() -> User:
    return User.objects.create_user(
        email="agent@thetaanalytics.in",
        password=PASSWORD,
        full_name="Field Agent",
        role=Role.FIELD_AGENT,
        district_ids=[9001, 9002],
    )


@pytest.fixture
def data_ops() -> User:
    return User.objects.create_user(
        email="ops@thetaanalytics.in",
        password=PASSWORD,
        full_name="Data Ops",
        role=Role.DATA_OPS,
    )


@pytest.fixture
def client() -> APIClient:
    return APIClient()


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------


def test_healthz_is_unauthenticated(client):
    """The ALB probe must not need a token."""
    response = client.get("/api/v1/healthz/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_generates(client, agent):
    """Doc 03 §10: contract drift fails the build, so the schema must build."""
    client.force_authenticate(user=agent)
    response = client.get("/api/schema/")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Auth (Doc 11 §2)
# ---------------------------------------------------------------------------


def test_login_returns_token_pair_and_user(client, agent):
    response = client.post(
        reverse("login"),
        {"email": agent.email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200
    body = response.json()
    assert "access" in body
    assert "refresh" in body
    assert body["user"]["role"] == Role.FIELD_AGENT
    assert body["user"]["district_ids"] == [9001, 9002]


def test_login_rejects_bad_password(client, agent):
    response = client.post(
        reverse("login"),
        {"email": agent.email, "password": "wrong"},
        format="json",
    )
    assert response.status_code == 401
    # Uniform error envelope (Doc 11 §1)
    assert response.json()["error"]["code"] == "unauthenticated"


def test_me_returns_role_and_territory(client, agent):
    client.force_authenticate(user=agent)
    body = client.get(reverse("me")).json()
    assert body["email"] == agent.email
    assert body["district_ids"] == [9001, 9002]
    assert body["is_cross_territory"] is False


def test_protected_endpoint_requires_auth(client):
    assert client.get(reverse("me")).status_code == 401


# ---------------------------------------------------------------------------
# MFA (Doc 12 §1)
# ---------------------------------------------------------------------------


@pytest.mark.compliance
@pytest.mark.parametrize("role", sorted(MFA_REQUIRED_ROLES))
def test_mfa_enforced_for_privileged_roles(role):
    """🔴 MFA is mandatory for data_ops, campaign_manager, compliance, admin."""
    user = User.objects.create_user(
        email=f"{role}@thetaanalytics.in",
        password=PASSWORD,
        full_name=role,
        role=role,
    )
    assert user.mfa_enforced is True


@pytest.mark.compliance
def test_mfa_flag_cannot_be_disabled_by_hand(data_ops):
    """
    An admin editing a user must not be able to turn MFA off for a role that
    requires it. save() derives the flag rather than trusting the input.
    """
    data_ops.mfa_enforced = False
    data_ops.save()
    data_ops.refresh_from_db()
    assert data_ops.mfa_enforced is True


def test_mfa_not_enforced_for_field_agent(agent):
    assert agent.mfa_enforced is False


def test_login_reports_mfa_requirement(client, data_ops):
    body = client.post(
        reverse("login"),
        {"email": data_ops.email, "password": PASSWORD},
        format="json",
    ).json()
    assert body["mfa_required"] is True
    assert body["mfa_enrolled"] is False


# ---------------------------------------------------------------------------
# RLS context (Doc 12 §3)
# ---------------------------------------------------------------------------


def test_rls_context_shape(agent):
    """The three session variables the RLS policy reads."""
    context = agent.rls_context()
    assert context["app.user_role"] == Role.FIELD_AGENT
    assert context["app.user_districts"] == "9001,9002"
    assert context["app.user_id"] == str(agent.pk)


def test_empty_territory_fails_closed(client):
    """
    A scoped role with no districts sees nothing. Failing closed is deliberate:
    a half-configured user must not default to seeing everything.
    """
    user = User.objects.create_user(
        email="new@thetaanalytics.in",
        password=PASSWORD,
        full_name="Unassigned",
        role=Role.FIELD_AGENT,
    )
    assert user.district_csv == ""
    assert user.is_cross_territory is False


@pytest.mark.parametrize("role", [Role.DATA_OPS, Role.COMPLIANCE, Role.ADMIN, Role.LEADERSHIP])
def test_cross_territory_roles(role):
    user = User(email=f"x-{role}@t.in", full_name="x", role=role)
    assert user.is_cross_territory is True


# ---------------------------------------------------------------------------
# PII scrubbing (R8, R12)
# ---------------------------------------------------------------------------


@pytest.mark.compliance
@pytest.mark.parametrize(
    "raw,redacted",
    [
        ("call +919876543210 now", "[PHONE_REDACTED]"),
        ("mobile 9876543210", "[PHONE_REDACTED]"),
        ("mail ramesh@gmail.com", "[EMAIL_REDACTED]"),
        ("uid 4321 8765 2109", "[AADHAAR_REDACTED]"),
        ("pan ABCDE1234F", "[PAN_REDACTED]"),
    ],
)
def test_scrub_redacts_pii(raw, redacted):
    """🔴 R8/R12: logs are kept a year. Nothing personal may reach them."""
    out = scrub(raw)
    assert redacted in out
    # Whatever the label, the underlying value must be gone.
    for token in ("9876543210", "4321 8765 2109", "ramesh@gmail.com", "ABCDE1234F"):
        assert token not in out


@pytest.mark.compliance
def test_scrub_mapping_drops_sensitive_keys():
    out = scrub_mapping(
        {
            "farmer_id": "uuid-1234",
            "aadhaar": "432187652109",
            "password": "hunter2",
            "nested": {"token": "abc", "note": "ring 9876543210"},
        }
    )
    assert out["farmer_id"] == "uuid-1234"  # identifiers are fine to log
    assert out["aadhaar"] == "[REDACTED]"
    assert out["password"] == "[REDACTED]"
    assert out["nested"]["token"] == "[REDACTED]"
    assert "9876543210" not in out["nested"]["note"]


@pytest.mark.compliance
def test_aadhaar_not_partially_eaten_by_phone_rule():
    """
    Pattern order matters. A 12-digit Aadhaar starting 9 would match the phone
    rule first and leave two digits behind, which is worse than useless.
    """
    assert scrub("987654321098") == "[AADHAAR_REDACTED]"


# ---------------------------------------------------------------------------
# API contract (Doc 03 §10)
# ---------------------------------------------------------------------------


def test_openapi_schema_has_no_errors():
    """
    Doc 03 §10: "contract drift fails the build".

    drf-spectacular emits an error for every view it cannot infer a shape for,
    and those become `any` in the generated TypeScript client. Failing here is
    how the typed-client guarantee stays real instead of aspirational.
    """
    from drf_spectacular.drainage import GENERATOR_STATS
    from drf_spectacular.generators import SchemaGenerator

    GENERATOR_STATS.reset()
    schema = SchemaGenerator().get_schema(request=None, public=True)

    assert schema["paths"], "schema generated no paths"

    errors = dict(GENERATOR_STATS._error_cache)
    assert not errors, (
        "OpenAPI generation produced errors — each one becomes an `any` in the "
        f"generated TypeScript client:\n{chr(10).join(errors)}"
    )


def test_every_endpoint_is_documented():
    """An undocumented endpoint is one the frontend has to guess at."""
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)
    undocumented = [
        f"{method.upper()} {path}"
        for path, methods in schema["paths"].items()
        for method, op in methods.items()
        if not op.get("summary") and not op.get("description")
    ]
    assert not undocumented, f"missing summary/description: {undocumented}"


# ---------------------------------------------------------------------------
# MFA enrolment (Doc 12 §1)
# ---------------------------------------------------------------------------


def _current_code(device) -> str:
    """The code an authenticator app would be showing right now."""
    import time

    from django_otp.oath import TOTP

    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time()
    return str(totp.token()).zfill(device.digits)


def test_enrol_returns_qr_and_secret(client, data_ops):
    """Pasting a raw otpauth:// URI into a phone is miserable; offer both."""
    client.force_authenticate(user=data_ops)
    body = client.post(reverse("mfa-enrol")).json()

    assert body["provisioning_uri"].startswith("otpauth://totp/")
    assert body["secret"], "manual-entry secret missing"
    assert "<svg" in body["qr_svg"]
    assert body["already_confirmed"] is False


@pytest.mark.compliance
def test_enrolment_completes_on_first_valid_code(client, data_ops):
    """
    🔴 Regression: enrolment created an unconfirmed device while verification
    only searched confirmed ones, so nothing ever flipped the flag and MFA
    could never be completed. Every code was rejected as invalid.

    Proving you can read a code off the device is exactly what confirms the
    authenticator holds the right secret, so a first valid code must confirm it.
    """
    from django_otp.plugins.otp_totp.models import TOTPDevice

    client.force_authenticate(user=data_ops)
    client.post(reverse("mfa-enrol"))

    device = TOTPDevice.objects.get(user=data_ops)
    assert device.confirmed is False, "enrolment should start unconfirmed"

    response = client.post(reverse("mfa-verify"), {"token": _current_code(device)}, format="json")
    assert response.status_code == 200, response.json()
    assert "access" in response.json()

    device.refresh_from_db()
    assert device.confirmed is True, "a valid code must confirm the device"


@pytest.mark.compliance
def test_verified_token_carries_mfa_satisfied(client, data_ops):
    """
    The re-issued token must say MFA is satisfied, or the client keeps a token
    whose claim still says otherwise and every protected call keeps failing.
    """
    import base64
    import json as _json

    from django_otp.plugins.otp_totp.models import TOTPDevice

    client.force_authenticate(user=data_ops)
    client.post(reverse("mfa-enrol"))
    device = TOTPDevice.objects.get(user=data_ops)

    body = client.post(
        reverse("mfa-verify"), {"token": _current_code(device)}, format="json"
    ).json()

    payload = body["access"].split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = _json.loads(base64.urlsafe_b64decode(payload))

    assert claims["mfa_satisfied"] is True
    assert claims["mfa_required"] is True


def test_wrong_code_is_rejected(client, data_ops):
    client.force_authenticate(user=data_ops)
    client.post(reverse("mfa-enrol"))

    response = client.post(reverse("mfa-verify"), {"token": "000000"}, format="json")
    assert response.status_code == 400
