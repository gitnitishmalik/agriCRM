"""
Permission classes shared across the API.

🔴 Doc 12 §2: `contact.view_full` and `import.commit` are the two permissions
to grant sparingly and review quarterly. They are named here so a grep finds
every enforcement point.
"""

from rest_framework.permissions import BasePermission

VIEW_FULL_CONTACT = "organisations.view_full_contact"
IMPORT_COMMIT = "dataquality.commit_import"


class IsMFAVerified(BasePermission):
    """
    Blocks a role that requires MFA from acting on a token that has not
    satisfied it. The claim is written at login and refreshed by
    /auth/mfa/verify/, so this cannot be bypassed by a client that simply
    declines to call the verify endpoint.
    """

    message = "MFA verification required for this role."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user.is_authenticated:
            return False
        if not user.mfa_enforced:
            return True
        token = getattr(request.auth, "payload", None) or {}
        return bool(token.get("mfa_satisfied"))


class CanViewFullContact(BasePermission):
    """Gate on unmasking a phone or email. Every grant writes an access log."""

    def has_permission(self, request, view) -> bool:
        return request.user.has_perm(VIEW_FULL_CONTACT)
