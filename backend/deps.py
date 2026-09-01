"""
Request dependencies — who is calling, and what they are allowed to do.

🔴 `require_user` is the authenticated caller. `require_verified_user` is the
authenticated caller who has also cleared the second factor, and it is what
every business route depends on.

The distinction matters more than it reads. Under Django this project shipped
a phase with `IsMFAVerified` written, tested and attached to nothing: the
class was correct and the default permission list did not include it, so every
organisation and invoice endpoint served a privileged pre-MFA token. The fix
there was to make enforcement the default and force an endpoint to opt out
loudly. The same shape applies here — `PRE_MFA` below is the opt-out list, a
test walks the router and fails on anything reachable that is not on it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db import get_session
from backend.models.accounts import User
from backend.security import decode, mfa_is_satisfied

#: Endpoints reachable *before* the second factor, and why. A privileged user
#: has to be able to reach these holding a token that has not satisfied MFA —
#: without them the requirement is not a security control, it is a lockout.
PRE_MFA: dict[str, str] = {
    "healthz": "Unauthenticated liveness probe. A load balancer has no token.",
    "healthz_alias": (
        "The same probe without the trailing slash. Registered rather than "
        "redirected: a health checker that receives a 307 may score the "
        "instance as down, and some do not follow redirects at all."
    ),
    "readyz_alias": "As `readyz`, without the trailing slash. See `healthz_alias`.",
    "readyz": (
        "Readiness probe. Reports only whether a database round trip succeeded "
        "and returns no data of any kind, so it needs no credentials."
    ),
    "login": "Issues the pair whose MFA claim everything else then checks.",
    "refresh": (
        "A 15-minute access token can expire mid-enrolment — scanning a QR code "
        "is slow. Refresh copies the MFA claim forward rather than granting it."
    ),
    "logout": "Abandoning a half-finished sign-in must not need a second factor.",
    "me": (
        "The client cannot know to send the user to /mfa until it has read "
        "`mfa_enforced` off this. Returns the caller's own row, no business data."
    ),
    "mfa_enrol": "The second factor itself. Requiring MFA to set up MFA is a deadlock.",
    "mfa_verify": "As above — this is what turns the claim on.",
    "payment_webhook": (
        "🔴 A payment gateway has no user, no token and no second factor. Its "
        "authentication is an HMAC over the raw request bytes, checked in "
        "`domain/payments.ingest_webhook` before the event is acted on — and "
        "the event is stored with its verdict either way, so a rejected one "
        "leaves evidence. It reads nothing and can only create a payment when "
        "the signature verifies, the event is fresh and the amount, currency "
        "and reference all match an outstanding request."
    ),
    "whatsapp_verify": (
        "🔴 Meta's subscription handshake, which happens before any user or "
        "token exists. It compares a configured verify token in constant time "
        "and echoes a challenge; it reads nothing, writes nothing, and refuses "
        "outright when no token is configured."
    ),
    "whatsapp_inbound": (
        "🔴 A messaging provider has no user, no token and no second factor. "
        "Authentication is an HMAC over the raw request bytes plus a sender "
        "bound to exactly one billing entity in `crm.messaging_identity` — an "
        "unknown number is recorded and answered with silence, because "
        "replying at all confirms the endpoint is live. A message can produce "
        "a draft proposal and nothing else; the copilot's action vocabulary "
        "has no word for issue, cancel, pay or send."
    ),
}

bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


class Caller:
    """The authenticated user plus the claims of the token they presented."""

    def __init__(self, user: User, claims: dict):
        self.user = user
        self.claims = claims

    @property
    def mfa_satisfied(self) -> bool:
        return mfa_is_satisfied(self.claims, user_requires_mfa=self.user.requires_mfa)


async def require_user(
    request: Request, session: SessionDep, credentials: CredentialsDep
) -> Caller:
    """Authenticated, but not necessarily past the second factor."""
    if not settings.auth_enabled:
        # 🔴 Development-only. Guarded by `debug` in `config.auth_enabled`, so
        # it cannot be switched on for a deployed instance by an env var alone.
        user = await _bypass_user(session)
        return Caller(user, {"role": user.role, "mfa_required": False, "mfa_satisfied": True})

    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = decode(credentials.credentials)
    except JWTError as error:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Token is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    user = await session.scalar(select(User).where(User.id == claims.get("user_id")))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No such active user.")

    request.state.caller = user
    return Caller(user, claims)


async def require_verified_user(
    caller: Annotated[Caller, Depends(require_user)],
) -> Caller:
    """
    🔴 Authenticated *and* past the second factor. What business routes use.

    403 rather than 401, deliberately: the credentials are perfectly valid and
    the caller is who they say they are. What is missing is a step they can go
    and complete, and a 401 would tell the client to throw the token away and
    start over.
    """
    if not caller.mfa_satisfied:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "MFA verification required for this role.",
        )
    return caller


async def _bypass_user(session: AsyncSession) -> User:
    """
    The fixed low-privilege user every request becomes when auth is off.

    🔴 A field agent with no territory, never an admin. A bypass that granted
    admin would hide every permission bug until the day auth came back on.
    """
    from backend.models.accounts import User as UserModel

    email = "dev-no-auth@agricrm.local"
    user = await session.scalar(select(UserModel).where(UserModel.email == email))
    if user is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"DEV_NO_AUTH is on but {email} does not exist. "
            "Run the Django service once, or seed it, to create the bypass user.",
        )
    return user


CurrentUser = Annotated[Caller, Depends(require_verified_user)]
AnyUser = Annotated[Caller, Depends(require_user)]


# ---------------------------------------------------------------------------
# 🔴 Unknown query parameters are an error, not a shrug
# ---------------------------------------------------------------------------

#: Parameters every list endpoint accepts regardless of what it filters on.
PLUMBING_PARAMS = frozenset({"limit", "offset", "cursor", "ordering", "format"})


async def reject_unknown_filters(request: Request) -> None:
    """
    400 on a query parameter the endpoint does not declare.

    🔴 FastAPI ignores extras by default. Django did not, deliberately, and the
    reason is in CLAUDE.md: a typo'd filter that silently does nothing is how
    someone exports the whole registry believing they exported one district.
    `?statu=issued` returning every invoice — quietly, with a 200 — is worse
    than any error, because the caller has no way to notice.

    Losing that on the way to FastAPI would be exactly the sort of regression a
    migration introduces: nothing fails, a control simply stops existing. So it
    is reinstated here as a dependency, and `tests/test_billing.py` holds it.

    The declared names are read off the route's own signature, so this cannot
    drift from what the endpoint actually accepts — there is no second list to
    keep in step.
    """
    route = request.scope.get("route")
    if route is None:
        return

    declared = {param.alias or param.name for param in getattr(route.dependant, "query_params", [])}

    unknown = sorted(set(request.query_params) - declared - PLUMBING_PARAMS)
    if unknown:
        accepted = sorted(declared | PLUMBING_PARAMS)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported filter(s): {', '.join(unknown)}. "
            f"This endpoint accepts: {', '.join(accepted)}.",
        )


#: Attach to any endpoint that filters. Reads the route's own parameters, so
#: adding a filter needs no change here.
StrictQuery = Depends(reject_unknown_filters)
