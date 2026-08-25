"""
Uniform error envelope for the API (Doc 11 §1).

Every error response carries a machine-readable code and the request_id, so a
support conversation can be traced from a screenshot to a log line.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)

_CODE_BY_STATUS = {
    400: "validation_error",
    401: "unauthenticated",
    403: "permission_denied",
    404: "not_found",
    409: "conflict",
    422: "unprocessable",
    429: "rate_limited",
    500: "internal_error",
}

#: Codes that say something the HTTP status cannot. A client distinguishes
#: "you may not do this" from "this recipient has not consented", and the
#: second is a compliance signal worth surfacing distinctly (Doc 11 §1).
_DOMAIN_CODES = frozenset({"consent_required", "legal_basis_not_confirmed", "source_not_approved"})


class ConsentRequired(exceptions.APIException):
    """🔴 Raised when a send is attempted to a non-consented recipient."""

    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Recipient has no valid consent for this channel and purpose."
    default_code = "consent_required"


class LegalBasisNotConfirmed(exceptions.APIException):
    """🔴 R5 — an import cannot commit until a named user asserts lawful basis."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "legal_basis_confirmed must be set by a named user before commit."
    default_code = "legal_basis_not_confirmed"


class DuplicateCandidate(exceptions.APIException):
    """409 with candidate matches, so the create form can block (Doc 11 §3)."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


def agricrm_exception_handler(exc, context):
    # 🔴 Doc 11 §1: out-of-territory reads return 404, never 403. A 403 confirms
    # the record exists, which leaks the existence of accounts in other
    # territories. RLS filters the row out, so this mostly happens naturally —
    # this mapping makes it happen deliberately too.
    if isinstance(exc, PermissionDenied) and getattr(exc, "hide_existence", False):
        exc = Http404()

    response = exception_handler(exc, context)
    request = context.get("request")
    request_id = getattr(request, "request_id", None)

    if response is None:
        logger.exception("Unhandled API exception", extra={"request_id": request_id})
        return Response(
            {
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                    "details": {},
                    "request_id": request_id,
                }
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    detail = response.data

    # Doc 11 §1 fixes the code for each status. DRF's own codes are more
    # granular than the contract ("authentication_failed" vs "unauthenticated"),
    # so the status mapping wins — except for the domain codes we define below,
    # which carry meaning the status alone cannot express.
    exc_code = getattr(exc, "default_code", None)
    if exc_code in _DOMAIN_CODES:
        code = exc_code
    else:
        code = _CODE_BY_STATUS.get(response.status_code, "error")

    if isinstance(detail, dict) and "detail" in detail:
        message, details = str(detail["detail"]), {}
    elif isinstance(detail, dict):
        message, details = "Invalid input", detail
    else:
        message, details = str(detail), {}

    response.data = {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }
    return response
