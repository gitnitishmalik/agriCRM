"""
Admin session handling.

🔴 **The session cookie carries the same JWT the API issues.** Not a second
credential — the same token, the same claims, the same `mfa_satisfied` flag,
verified by the same `security.decode`. A separate admin session store would
be a second authentication system with a second set of bugs, and the one thing
worse than an admin panel is an admin panel that authenticates differently
from the API it fronts.

The cookie exists only because a browser navigating between pages cannot send
an `Authorization` header. It is `HttpOnly` (so no script can read it),
`SameSite=Lax` (so another site cannot make the browser use it), and `Secure`
whenever the service is not in debug.

🔴 **CSRF is enforced on every mutating request.** `SameSite=Lax` blocks the
cross-site form POST, and a per-session token blocks the rest. A `GET` in this
package never mutates, which is what makes that split safe.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db import get_session
from backend.deps import Caller
from backend.models.accounts import User
from backend.security import decode, mfa_is_satisfied

SESSION_COOKIE = "agricrm_admin"
CSRF_COOKIE = "agricrm_admin_csrf"

#: The admin is a data-operations console. 🔴 `field_agent` is deliberately
#: absent: an agent's job is the field app, and a console that lists every
#: organisation and every invoice is not the surface to hand them.
ADMIN_ROLES = frozenset(
    {"bd_manager", "project_manager", "data_ops", "leadership", "compliance", "admin"}
)


class LoginRequired(Exception):
    """Raised to send the browser to the sign-in page rather than a JSON 401."""

    def __init__(self, next_url: str = "/admin/") -> None:
        self.next_url = next_url


def set_session(response: Response, access_token: str) -> str:
    """Attach the session and CSRF cookies. Returns the CSRF token."""
    csrf = secrets.token_urlsafe(32)

    response.set_cookie(
        SESSION_COOKIE,
        access_token,
        httponly=True,
        samesite="lax",
        secure=not settings.debug,
        path="/admin",
        max_age=settings.access_token_minutes * 60,
    )
    # 🔴 Readable by script on purpose — the form needs to echo it back, and
    # its secrecy is not what makes it work. What makes it work is that a
    # cross-site attacker cannot read a cookie from another origin to copy it
    # into their own form.
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        httponly=False,
        samesite="lax",
        secure=not settings.debug,
        path="/admin",
        max_age=settings.access_token_minutes * 60,
    )
    return csrf


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/admin")
    response.delete_cookie(CSRF_COOKIE, path="/admin")


async def current_admin(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Caller:
    """
    The signed-in admin user, or a redirect to the sign-in page.

    🔴 MFA is enforced exactly as it is on the API. A privileged role holding a
    pre-MFA token reaches the sign-in page, not the console — the Django
    service once shipped a phase where the permission class existed and was
    attached to nothing, and this is the same lesson applied to a second
    surface.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise LoginRequired(str(request.url.path))

    try:
        claims = decode(token)
    except JWTError as error:
        raise LoginRequired(str(request.url.path)) from error

    user = await session.scalar(select(User).where(User.id == claims.get("user_id")))
    if user is None or not user.is_active:
        raise LoginRequired(str(request.url.path))

    if not mfa_is_satisfied(claims, user_requires_mfa=user.requires_mfa):
        raise LoginRequired(str(request.url.path))

    if user.role not in ADMIN_ROLES:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"The admin console is for data operations. Your role ({user.role}) "
            f"does not have access to it.",
        )

    caller = Caller(user, claims)
    request.state.caller = user
    return caller


def check_csrf(request: Request) -> None:
    """
    🔴 Compare the form's token with the cookie's, in constant time.

    A missing token is a failure, not a pass. An endpoint that skips the check
    when the field is absent has a CSRF defence any attacker can opt out of by
    omitting a field.
    """
    cookie = request.cookies.get(CSRF_COOKIE, "")
    submitted = getattr(request.state, "csrf_token", "")

    if not cookie or not submitted or not hmac.compare_digest(cookie, submitted):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This form has expired or was not submitted from the console. "
            "Reload the page and try again.",
        )


def redirect_to_login(next_url: str = "/admin/") -> RedirectResponse:
    from urllib.parse import quote

    return RedirectResponse(
        f"/admin/login?next={quote(next_url, safe='')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


AdminUser = Annotated[Caller, Depends(current_admin)]
