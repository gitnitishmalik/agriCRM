"""
Authentication (Doc 11 §2, Doc 12 §1).

Every route here is in `deps.PRE_MFA` except the password change, and that
exclusion is deliberate: someone holding a stolen password and nothing else
must not be able to replace it with one they chose. That is the single thing
the second factor exists to stop, and a pre-MFA password change hands the
account over permanently.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status
from jose import JWTError
from sqlalchemy import delete, select

from backend.config import settings
from backend.deps import AnyUser, CurrentUser, SessionDep
from backend.models.accounts import MFA_REQUIRED_ROLES, TOTPDevice, User
from backend.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MFAEnrolResponse,
    MFAVerifyRequest,
    PasswordChangeRequest,
    RefreshRequest,
    TokenPair,
    UserOut,
)
from backend.security import (
    decode,
    hash_password,
    hex_key_to_base32,
    issue_pair,
    refresh_pair,
    validate_new_password,
    verify_password,
    verify_totp_counter,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
LOGIN_FAILURE_LIMIT = 10
LOGIN_FAILURE_WINDOW_MINUTES = 15
# Makes a nonexistent account take the same PBKDF2 path as a real account.
DUMMY_PASSWORD_HASH = hash_password("not-a-real-user-password")


async def _register_refresh(session: SessionDep, token: str, user_id: int):
    """Put a refresh token in simplejwt's shared outstanding-token table."""
    from datetime import UTC, datetime

    from backend.models.accounts import OutstandingToken

    claims = decode(token, expected_type="refresh")
    existing = await session.scalar(
        select(OutstandingToken).where(OutstandingToken.jti == claims["jti"])
    )
    if existing is not None:
        return existing
    outstanding = OutstandingToken(
        user_id=user_id,
        jti=claims["jti"],
        token=token,
        created_at=datetime.fromtimestamp(claims["iat"], UTC),
        expires_at=datetime.fromtimestamp(claims["exp"], UTC),
    )
    session.add(outstanding)
    await session.flush()
    return outstanding


async def _blacklist(session: SessionDep, outstanding) -> None:
    from datetime import UTC, datetime

    from backend.models.accounts import BlacklistedToken

    existing = await session.scalar(
        select(BlacklistedToken).where(BlacklistedToken.token_id == outstanding.id)
    )
    if existing is None:
        session.add(BlacklistedToken(token_id=outstanding.id, blacklisted_at=datetime.now(UTC)))


def _qr_svg(data: str) -> str:
    import io

    import qrcode
    import qrcode.image.svg

    output = io.BytesIO()
    image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    image.save(output)
    return output.getvalue().decode("utf-8")


def _user_out(user: User, *, mfa_satisfied: bool) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        district_ids=list(user.district_ids or []),
        is_cross_territory=user.is_cross_territory,
        mfa_enforced=user.mfa_enforced,
        mfa_satisfied=mfa_satisfied,
    )


@router.post("/login/", response_model=LoginResponse, name="login")
async def login(payload: LoginRequest, request: Request, session: SessionDep) -> LoginResponse:
    """
    Sign in. Issues a pair either way; a privileged role's opens nothing until
    the second factor is verified.
    """
    from datetime import UTC, datetime, timedelta

    from backend.models.accounts import AccessAttempt

    email = payload.email.strip().lower()
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")[:255]
    attempt_query = select(AccessAttempt).where(
        AccessAttempt.username == email,
        AccessAttempt.user_agent == user_agent,
        AccessAttempt.ip_address == ip_address,
    )
    attempt = await session.scalar(attempt_query)
    now = datetime.now(UTC)
    if attempt and attempt.attempt_time < now - timedelta(minutes=LOGIN_FAILURE_WINDOW_MINUTES):
        attempt.failures_since_start = 0
    if attempt and attempt.failures_since_start >= LOGIN_FAILURE_LIMIT:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed sign-in attempts. Try again in 15 minutes.",
            headers={"Retry-After": str(LOGIN_FAILURE_WINDOW_MINUTES * 60)},
        )

    user = await session.scalar(select(User).where(User.email == email))
    password_matches = verify_password(
        payload.password, user.password if user else DUMMY_PASSWORD_HASH
    )

    # 🔴 One message for "no such user" and "wrong password". Distinguishing
    # them turns this endpoint into a way to enumerate who has an account.
    if user is None or not user.is_active or not password_matches:
        if attempt is None:
            attempt = AccessAttempt(
                username=email,
                ip_address=ip_address,
                user_agent=user_agent,
                http_accept=request.headers.get("accept", "")[:1025],
                path_info=request.url.path[:255],
                attempt_time=now,
                get_data="",
                post_data="",
                failures_since_start=1,
            )
            session.add(attempt)
        else:
            attempt.failures_since_start += 1
            attempt.attempt_time = now
        # Authentication failure is still a durable security event even
        # though the HTTP request itself ends with an exception.
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")

    await session.execute(delete(AccessAttempt).where(AccessAttempt.username == email))

    mfa_required = user.role in MFA_REQUIRED_ROLES and settings.require_mfa
    tokens = issue_pair(
        user.id,
        role=user.role,
        mfa_required=mfa_required,
        mfa_satisfied=not mfa_required,
    )
    await _register_refresh(session, tokens["refresh"], user.id)

    enrolled = await session.scalar(
        select(TOTPDevice).where(TOTPDevice.user_id == user.id, TOTPDevice.confirmed.is_(True))
    )

    return LoginResponse(
        **tokens,
        user=_user_out(user, mfa_satisfied=not mfa_required),
        mfa_required=mfa_required,
        mfa_enrolled=enrolled is not None,
    )


@router.post("/refresh/", response_model=TokenPair, name="refresh")
async def refresh(payload: RefreshRequest, session: SessionDep) -> TokenPair:
    """
    🔴 Copies the MFA claims forward; it does not mint them.

    Otherwise this is the way around the second factor: sign in, ignore the
    MFA screen, refresh once, hold a satisfied token having proved one factor.
    """
    try:
        tokens, claims = refresh_pair(payload.refresh)
    except JWTError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token is invalid.") from error

    from backend.models.accounts import BlacklistedToken, OutstandingToken

    old = await session.scalar(
        select(OutstandingToken).where(OutstandingToken.jti == claims["jti"])
    )
    if old is not None:
        revoked = await session.scalar(
            select(BlacklistedToken).where(BlacklistedToken.token_id == old.id)
        )
        if revoked is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token is revoked.")

    user = await session.scalar(select(User).where(User.id == claims.get("user_id")))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token is invalid.")

    # Rotation is one-time: register and revoke the token being exchanged,
    # then register the replacement before returning it.
    old = old or await _register_refresh(session, payload.refresh, user.id)
    await _blacklist(session, old)
    await _register_refresh(session, tokens["refresh"], user.id)
    return TokenPair(**tokens)


@router.get("/me/", response_model=UserOut, name="me")
async def me(caller: AnyUser) -> UserOut:
    """
    The caller's own row. Reachable before MFA by design — the client cannot
    know to redirect to the second-factor screen until it has read this.
    """
    return _user_out(caller.user, mfa_satisfied=caller.mfa_satisfied)


@router.post("/mfa/enrol/", response_model=MFAEnrolResponse, name="mfa_enrol")
async def mfa_enrol(caller: AnyUser, session: SessionDep) -> MFAEnrolResponse:
    """
    Begin TOTP enrolment.

    The device is created unconfirmed and stays that way until the user proves
    they can read a code off it. 🔴 Checking only confirmed devices at verify
    time made enrolment impossible to finish — nothing else ever flips the
    flag — which was a real bug in the Django service before this migration.
    """
    from datetime import UTC, datetime

    device = await session.scalar(select(TOTPDevice).where(TOTPDevice.user_id == caller.user.id))

    if device is None:
        device = TOTPDevice(
            user_id=caller.user.id,
            name="default",
            confirmed=False,
            key=secrets.token_hex(20),  # django-otp stores the secret as hex
            step=30,
            t0=0,
            digits=6,
            tolerance=1,
            drift=0,
            last_t=-1,
            throttling_failure_count=0,
            created_at=datetime.now(UTC),
        )
        session.add(device)
        await session.flush()

    secret = hex_key_to_base32(device.key)
    issuer = "AgriCRM"
    uri = (
        f"otpauth://totp/{issuer}:{caller.user.email}"
        f"?secret={secret}&issuer={issuer}&digits={device.digits}&period={device.step}"
    )

    return MFAEnrolResponse(
        provisioning_uri=uri,
        secret=secret,
        qr_svg=_qr_svg(uri),
        already_confirmed=device.confirmed,
    )


async def verify_totp_for_user(session, user, token: str) -> bool:
    """
    Check one TOTP code against a user's enrolled device.

    🔴 Extracted so the admin console's sign-in form uses *this* rather than a
    second copy. What lives here is not "compare six digits" — it is the
    exponential throttle on repeated failures, the replay defence that refuses
    a counter already used, and the clock-drift window. A second surface
    reimplementing those would get one of them subtly wrong, and the one it
    would get wrong is the replay check, because it is the one that looks
    redundant.

    Raises `HTTPException` for a throttled device (429) and for no enrolment
    (400); returns False for a code that is simply wrong.
    """
    from datetime import UTC, datetime, timedelta

    device = await session.scalar(select(TOTPDevice).where(TOTPDevice.user_id == user.id))
    if device is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No authenticator enrolled.")

    now = datetime.now(UTC)
    if device.throttling_failure_count and device.throttling_failure_timestamp:
        wait_seconds = min(2 ** (device.throttling_failure_count - 1), 300)
        retry_at = device.throttling_failure_timestamp + timedelta(seconds=wait_seconds)
        if now < retry_at:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many invalid MFA codes. Try again shortly.",
                headers={"Retry-After": str(max(1, int((retry_at - now).total_seconds())))},
            )

    valid, counter, new_drift = verify_totp_counter(
        hex_key_to_base32(device.key),
        token,
        step=device.step,
        digits=device.digits,
        tolerance=device.tolerance,
        drift=device.drift,
        last_t=device.last_t,
    )
    if not valid:
        device.throttling_failure_count += 1
        device.throttling_failure_timestamp = now
        await session.commit()
        return False

    device.last_t = counter
    device.drift = new_drift
    device.throttling_failure_count = 0
    device.throttling_failure_timestamp = None
    device.last_used_at = now

    # Proving you can read a code off the device is exactly what confirms the
    # authenticator holds the right secret.
    if not device.confirmed:
        device.confirmed = True

    return True


@router.post("/mfa/verify/", response_model=TokenPair, name="mfa_verify")
async def mfa_verify(payload: MFAVerifyRequest, caller: AnyUser, session: SessionDep) -> TokenPair:
    """
    Verify a code and re-issue the pair with `mfa_satisfied=true`.

    The re-issue matters: without it the client keeps a token whose claim still
    says MFA is outstanding, and every protected call keeps failing.
    """
    if not await verify_totp_for_user(session, caller.user, payload.token):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid MFA code.")

    tokens = issue_pair(
        caller.user.id, role=caller.user.role, mfa_required=True, mfa_satisfied=True
    )
    await _register_refresh(session, tokens["refresh"], caller.user.id)
    return TokenPair(**tokens)


@router.post("/logout/", status_code=status.HTTP_205_RESET_CONTENT, name="logout")
async def logout(payload: LogoutRequest, caller: AnyUser, session: SessionDep) -> Response:
    """
    Blacklist the refresh token. Access tokens expire on their own in 15m.

    Idempotent: a double-tapped sign-out button is not an error condition.
    """
    from backend.models.accounts import OutstandingToken

    try:
        claims = decode(payload.refresh, expected_type="refresh")
    except JWTError:
        return Response(status_code=status.HTTP_205_RESET_CONTENT)

    if claims.get("user_id") != caller.user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Refresh token belongs to another user.")

    outstanding = await session.scalar(
        select(OutstandingToken).where(OutstandingToken.jti == claims["jti"])
    )
    if outstanding is None:
        outstanding = await _register_refresh(session, payload.refresh, caller.user.id)

    await _blacklist(session, outstanding)

    return Response(status_code=status.HTTP_205_RESET_CONTENT)


@router.post("/password/change/", status_code=status.HTTP_204_NO_CONTENT, name="password_change")
async def change_password(
    payload: PasswordChangeRequest, caller: CurrentUser, session: SessionDep
) -> Response:
    """
    Change the password and revoke every outstanding session (Doc 12 §13).

    🔴 Depends on `CurrentUser`, not `AnyUser` — deliberately absent from
    PRE_MFA. Someone holding a stolen password and nothing else must not be
    able to replace it with one they chose.
    """
    from datetime import UTC, datetime

    from backend.models.accounts import BlacklistedToken, OutstandingToken

    if not verify_password(payload.current_password, caller.user.password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")

    password_errors = validate_new_password(
        payload.new_password, email=caller.user.email, full_name=caller.user.full_name
    )
    if password_errors:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "message": "New password is not acceptable.",
                "details": {"new_password": password_errors},
            },
        )

    caller.user.password = hash_password(payload.new_password)

    # Changing a password is what someone does when they think the old one
    # leaked. If the sessions opened with it keep working, the act achieved
    # nothing.
    outstanding = await session.scalars(
        select(OutstandingToken).where(OutstandingToken.user_id == caller.user.id)
    )
    for token in outstanding:
        exists = await session.scalar(
            select(BlacklistedToken).where(BlacklistedToken.token_id == token.id)
        )
        if exists is None:
            session.add(BlacklistedToken(token_id=token.id, blacklisted_at=datetime.now(UTC)))

    return Response(status_code=status.HTTP_204_NO_CONTENT)
