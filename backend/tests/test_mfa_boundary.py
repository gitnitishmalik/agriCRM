"""
🔴 The security boundary, carried over from Phase 1.

The Django service shipped a phase where `IsMFAVerified` was written, unit
tested, referenced approvingly in three docstrings — and attached to nothing.
Every organisation and invoice endpoint inherited `IsAuthenticated` alone, so a
privileged user who ignored the frontend's redirect and called the API with
curl was served.

A migration is exactly when that kind of hole reopens: the class is gone, the
default permission list is gone, and the thing that replaces them is new code
nobody has attacked yet. These tests are the same assertions against the new
service, driven through HTTP.
"""

from __future__ import annotations

import base64
import json

import pytest

from backend.deps import PRE_MFA
from backend.tests.conftest import PASSWORD

pytestmark = pytest.mark.anyio

BUSINESS_ENDPOINTS = ["/api/v1/organisations/"]


def claims_of(access: str) -> dict:
    payload = access.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


async def login(client, user) -> dict:
    response = await client.post(
        "/api/v1/auth/login/", json={"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_login_locks_after_ten_failures(client, agent):
    headers = {"User-Agent": "agricrm-lockout-test"}
    for _ in range(10):
        response = await client.post(
            "/api/v1/auth/login/",
            json={"email": agent.email, "password": "definitely-wrong"},
            headers=headers,
        )
        assert response.status_code == 401, response.text

    locked = await client.post(
        "/api/v1/auth/login/",
        json={"email": agent.email, "password": PASSWORD},
        headers=headers,
    )
    assert locked.status_code == 429, locked.text


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Enforcement is the default, and the exceptions are declared
# ---------------------------------------------------------------------------


async def test_every_route_is_mfa_gated_unless_it_says_why():
    """
    🔴 The regression this file exists for.

    Walks the actual router. A route reachable before the second factor has to
    appear in `deps.PRE_MFA` with a reason a human reads in a diff — the
    question is never "did someone remember to add the dependency", it is "did
    someone remove it, and did they say why".
    """
    from backend.main import app
    from backend.routing import iter_routes

    unguarded: list[str] = []

    # 🔴 A slash alias is the *same endpoint function* as the route it aliases,
    # registered under the other trailing-slash form by
    # `routing.register_slash_aliases`. So an exemption declared for the base
    # route covers its alias, and resolving that here is what stops PRE_MFA
    # needing a second hand-written entry per route — the kind of list that
    # goes stale silently.
    #
    # Resolved by endpoint identity, never by the `_alias` name suffix: a name
    # is something a person types, and a route called `something_alias` must not
    # be able to inherit an exemption it was never granted.
    exempt_endpoints = {
        route.endpoint for route in iter_routes(app) if getattr(route, "name", None) in PRE_MFA
    }

    for route in iter_routes(app):
        name = getattr(route, "name", None)
        path = getattr(route, "path", "")
        if not name or not path.startswith("/api/v1"):
            continue
        if name in PRE_MFA or route.endpoint in exempt_endpoints:
            continue

        # `require_verified_user` is the gate. Its presence in the dependency
        # tree is what makes a route MFA-enforced.
        dependencies = str(getattr(route, "dependant", ""))
        source = repr(getattr(route, "endpoint", ""))
        gated = "require_verified_user" in dependencies or "CurrentUser" in source

        if not gated:
            # Fall back to inspecting the signature, which is where the
            # annotated dependency actually lives.
            import inspect

            try:
                signature = inspect.signature(route.endpoint)
                gated = any(
                    "CurrentUser" in str(param.annotation)
                    for param in signature.parameters.values()
                )
            except (TypeError, ValueError):
                gated = False

        if not gated:
            unguarded.append(f"{name} ({path})")

    assert unguarded == [], (
        "these routes are reachable without the second factor and are not "
        f"declared in backend.deps.PRE_MFA: {unguarded}"
    )


async def test_every_pre_mfa_exemption_carries_a_reason():
    """A blank reason is how the list grows without anyone noticing."""
    for name, reason in PRE_MFA.items():
        assert len(reason) > 30, f"{name} has no real explanation: {reason!r}"


# ---------------------------------------------------------------------------
# Behaviour at real endpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", BUSINESS_ENDPOINTS)
async def test_a_non_mfa_user_reaches_a_business_endpoint(client, agent, endpoint):
    """
    Field agents have no second factor and must not acquire one by accident.
    Enforcement that locks out the largest role is not enforcement, it is an
    outage.
    """
    tokens = await login(client, agent)
    assert tokens["mfa_required"] is False

    response = await client.get(endpoint, headers=auth(tokens["access"]))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("endpoint", BUSINESS_ENDPOINTS)
async def test_a_privileged_pre_mfa_token_is_refused(client, data_ops, endpoint):
    """
    🔴 The exit-gate assertion. Login succeeds — it has to, or the user could
    never reach enrolment — and the token it returns opens nothing.
    """
    tokens = await login(client, data_ops)
    assert tokens["mfa_required"] is True
    assert claims_of(tokens["access"])["mfa_satisfied"] is False

    response = await client.get(endpoint, headers=auth(tokens["access"]))
    assert response.status_code == 403, response.text
    assert "MFA" in response.text


async def test_an_anonymous_request_is_refused(client):
    response = await client.get("/api/v1/organisations/")
    assert response.status_code == 401


async def test_a_tampered_mfa_claim_is_not_authentication(client, data_ops):
    """
    Rewrite `mfa_satisfied` to true and present it. The claim is inside the
    signature, so this is a 401 — the token no longer verifies at all, which
    is a stronger answer than 403.
    """
    tokens = await login(client, data_ops)
    header, _payload, signature = tokens["access"].split(".")

    forged_claims = claims_of(tokens["access"]) | {"mfa_satisfied": True}
    forged = base64.urlsafe_b64encode(json.dumps(forged_claims).encode()).decode().rstrip("=")

    response = await client.get(
        "/api/v1/organisations/", headers=auth(f"{header}.{forged}.{signature}")
    )
    assert response.status_code == 401


async def test_a_token_issued_before_a_promotion_does_not_survive_it(client, agent, session):
    """
    🔴 Role escalation must not inherit a satisfied second factor.

    A field agent's token says `mfa_satisfied: true` truthfully, because MFA
    did not apply to the role it was issued for. Promote that user and the
    claim is still in their browser — a privileged session that never proved a
    second factor, for as long as they keep refreshing.
    """
    tokens = await login(client, agent)
    assert (
        await client.get("/api/v1/organisations/", headers=auth(tokens["access"]))
    ).status_code == 200

    agent.role = "data_ops"
    agent.mfa_enforced = True
    await session.flush()

    response = await client.get("/api/v1/organisations/", headers=auth(tokens["access"]))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


async def test_refreshing_an_unverified_token_does_not_bypass_mfa(client, data_ops):
    """
    🔴 The obvious attack once the direct call is closed: keep the pre-MFA
    refresh token and trade it for a fresh access token. Refresh copies the
    claims forward; it does not mint new ones.
    """
    tokens = await login(client, data_ops)

    refreshed = await client.post("/api/v1/auth/refresh/", json={"refresh": tokens["refresh"]})
    assert refreshed.status_code == 200
    new = refreshed.json()

    assert claims_of(new["access"])["mfa_satisfied"] is False
    response = await client.get("/api/v1/organisations/", headers=auth(new["access"]))
    assert response.status_code == 403


async def test_refresh_cannot_be_repeated_into_a_verified_session(client, data_ops):
    """A bypass that needs patience is still a bypass."""
    token = (await login(client, data_ops))["refresh"]

    for _ in range(3):
        response = await client.post("/api/v1/auth/refresh/", json={"refresh": token})
        assert response.status_code == 200
        body = response.json()
        token = body["refresh"]
        assert claims_of(body["access"])["mfa_satisfied"] is False


async def test_refresh_rotation_revokes_the_token_that_was_exchanged(client, agent):
    tokens = await login(client, agent)
    first = await client.post("/api/v1/auth/refresh/", json={"refresh": tokens["refresh"]})
    assert first.status_code == 200, first.text

    replay = await client.post("/api/v1/auth/refresh/", json={"refresh": tokens["refresh"]})
    assert replay.status_code == 401, replay.text


async def test_logout_revokes_a_fastapi_refresh_token(client, agent):
    tokens = await login(client, agent)
    response = await client.post(
        "/api/v1/auth/logout/",
        json={"refresh": tokens["refresh"]},
        headers=auth(tokens["access"]),
    )
    assert response.status_code == 205, response.text
    assert (
        await client.post("/api/v1/auth/refresh/", json={"refresh": tokens["refresh"]})
    ).status_code == 401


async def test_an_access_token_is_not_a_refresh_token(client, agent):
    """
    Token type is checked. Without it, an access token would be accepted at
    the refresh endpoint and could be traded for a fresh pair forever — an
    access token that never expires.
    """
    tokens = await login(client, agent)
    response = await client.post("/api/v1/auth/refresh/", json={"refresh": tokens["access"]})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# What a privileged user can still reach before the second factor
# ---------------------------------------------------------------------------


async def test_identity_is_readable_before_mfa(client, data_ops):
    """
    The client cannot know to redirect to /mfa until it has read this, so it
    is reachable — and it reports the state that redirect depends on.
    """
    tokens = await login(client, data_ops)
    response = await client.get("/api/v1/auth/me/", headers=auth(tokens["access"]))

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == data_ops.email
    assert body["mfa_enforced"] is True
    assert body["mfa_satisfied"] is False


async def test_me_agrees_with_the_permission_gate(client, agent):
    """
    🔴 The two must never disagree. If `mfa_satisfied` said false while the API
    served the request, the app would bounce the user to a screen they do not
    need and cannot leave.
    """
    tokens = await login(client, agent)
    body = (await client.get("/api/v1/auth/me/", headers=auth(tokens["access"]))).json()

    assert body["mfa_satisfied"] is True
    assert (
        await client.get("/api/v1/organisations/", headers=auth(tokens["access"]))
    ).status_code == 200


async def test_changing_a_password_requires_the_second_factor(client, data_ops):
    """
    🔴 Deliberately not in PRE_MFA. Someone holding a stolen password and
    nothing else must not be able to replace it with one they chose.
    """
    tokens = await login(client, data_ops)
    response = await client.post(
        "/api/v1/auth/password/change/",
        json={"current_password": PASSWORD, "new_password": "a-different-long-password"},
        headers=auth(tokens["access"]),
    )
    assert response.status_code == 403


async def test_an_mfa_code_cannot_be_replayed(client, data_ops):
    import pyotp

    tokens = await login(client, data_ops)
    enrolled = await client.post("/api/v1/auth/mfa/enrol/", headers=auth(tokens["access"]))
    assert enrolled.status_code == 200, enrolled.text
    body = enrolled.json()
    assert "<svg" in body["qr_svg"]

    code = pyotp.TOTP(body["secret"]).now()
    first = await client.post(
        "/api/v1/auth/mfa/verify/",
        json={"token": code},
        headers=auth(tokens["access"]),
    )
    assert first.status_code == 200, first.text

    replay = await client.post(
        "/api/v1/auth/mfa/verify/",
        json={"token": code},
        headers=auth(tokens["access"]),
    )
    assert replay.status_code == 400, replay.text


# ---------------------------------------------------------------------------
# The admin console is a second authenticated surface
# ---------------------------------------------------------------------------

#: Console routes reachable without a session, and why. 🔴 The same shape as
#: `PRE_MFA`: an exemption has to be named here with a reason a human reads in
#: a diff, and the walk below fails on anything else.
ADMIN_PUBLIC = {
    "admin_login_form": "The sign-in form itself. Requiring a session to reach it is a deadlock.",
    "admin_login": "Verifies the credentials that create the session.",
    "admin_logout": "Abandoning a half-finished sign-in must not need a session.",
}


async def test_every_admin_route_requires_a_session():
    """
    🔴 The console is HTML rather than JSON, which changes nothing about who
    may read it.

    It lists every organisation, every invoice and every collected value —
    exactly the data the API guards — so it is walked with the same
    suspicion. A route reachable without `current_admin` has to be named in
    `ADMIN_PUBLIC` with a reason.
    """
    import inspect

    from backend.main import app
    from backend.routing import iter_routes

    unguarded: list[str] = []

    for route in iter_routes(app):
        name = getattr(route, "name", None)
        path = getattr(route, "path", "")
        if not name or not path.startswith("/admin"):
            continue
        if name in ADMIN_PUBLIC:
            continue

        try:
            signature = inspect.signature(route.endpoint)
            guarded = any(
                "AdminUser" in str(param.annotation) for param in signature.parameters.values()
            )
        except (TypeError, ValueError):
            guarded = False

        if not guarded:
            unguarded.append(f"{name} ({path})")

    assert unguarded == [], (
        "these console routes are reachable without a session and are not "
        f"declared in ADMIN_PUBLIC: {unguarded}"
    )


async def test_the_console_enforces_mfa_like_the_api(client, data_ops):
    """
    🔴 A privileged role holding a pre-MFA session reaches the sign-in page,
    not the console.

    `current_admin` calls the same `mfa_is_satisfied` the API dependency does.
    A console that accepted a password alone for `data_ops` would be a way
    around the second factor that happens to render HTML.
    """
    from backend.admin.security import SESSION_COOKIE
    from backend.security import issue_pair

    # The token a password-only sign-in produces: valid, and not MFA-satisfied.
    tokens = issue_pair(data_ops.id, role=data_ops.role, mfa_required=True, mfa_satisfied=False)

    response = await client.get(
        "/admin/organisations/",
        cookies={SESSION_COOKIE: tokens["access"]},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert "/admin/login" in response.headers["location"]


async def test_a_field_agent_cannot_reach_the_console(client, agent):
    """
    The console is a data-operations tool. An agent's surface is the field
    app, and a page listing every organisation and every invoice is not it.
    """
    from backend.admin.security import SESSION_COOKIE
    from backend.security import issue_pair

    tokens = issue_pair(agent.id, role=agent.role, mfa_required=False, mfa_satisfied=True)
    response = await client.get(
        "/admin/organisations/",
        cookies={SESSION_COOKIE: tokens["access"]},
        follow_redirects=False,
    )
    assert response.status_code == 403, response.text


async def test_a_console_mutation_needs_a_csrf_token(client, data_ops):
    """
    🔴 A missing token is a failure, not a pass.

    An endpoint that skips the check when the field is absent has a CSRF
    defence any attacker can opt out of by omitting a field.
    """
    from backend.admin.security import SESSION_COOKIE
    from backend.security import issue_pair

    tokens = issue_pair(data_ops.id, role=data_ops.role, mfa_required=True, mfa_satisfied=True)
    response = await client.post(
        "/admin/logout",
        cookies={SESSION_COOKIE: tokens["access"]},
        data={},
        follow_redirects=False,
    )
    assert response.status_code == 403, response.text
