"""
Live GSTIN verification — a provider-neutral lookup and a deterministic fake.

🔴 **No scraping.** INVOICE.md §12.4 is explicit: this goes through an approved
GST/GSP provider, never the public portal. A scraper here would be R3 in
miniature — a site that cannot be read without defeating its protections is a
site telling you not to read it, and the answer is a licence, not a better
scraper.

🔴 **Downtime is `verification_unavailable`, never "valid".** That is the whole
reason the status vocabulary has a word for it. A provider timeout that fell
back to "assume it's fine" would produce exactly the failure this feature
exists to prevent: an invoice issued against a cancelled registration, denying
the customer input credit and raising a mismatch on their return.

🔴 **Only the GSTIN is sent.** Never invoice lines, never amounts, never
anything else from the CRM. A lookup reveals business identity; enriching the
request with our own commercial data would be giving a vendor a customer list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from backend import gstin as gstin_lib

logger = logging.getLogger("backend.gstin_lookup")


@dataclass(frozen=True)
class LookupResult:
    """
    What a provider reports.

    `status` is one of `crm.gstin_verification_status`. `raw` is kept only long
    enough to hash — the caller stores the digest and the fields it uses, not
    the body (INVOICE.md §12.4: store the raw response outside the invoice row
    and retain its hash for audit).
    """

    status: str
    gstin: str
    provider: str
    provider_reference: str | None = None
    legal_name: str | None = None
    trade_name: str | None = None
    registration_type: str | None = None
    taxpayer_status: str | None = None
    effective_from: date | None = None
    cancellation_date: date | None = None
    principal_address: str | None = None
    state_code: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class GstinLookupProvider(Protocol):
    name: str

    async def lookup(self, gstin: str) -> LookupResult: ...


# ---------------------------------------------------------------------------
# The deterministic fake
# ---------------------------------------------------------------------------

#: Fixtures. 🔴 Deliberately small and clearly marked, per the build brief:
#: statutory data is not invented in tests, and these are constructed cases
#: plus the two real registrations this business bills under. The awkward
#: cases are here on purpose — an inactive registration, a cancelled one, a
#: legal-name mismatch, a government UIN — because those are the ones the
#: pre-issue checks have to catch.
FIXTURES: dict[str, dict[str, Any]] = {
    # Syngenta UP — the customer whose GSTIN the historical sheet recorded two
    # ways (INVOICE.md §3, D2).
    "09AAECS9424P1ZL": {
        "status": "valid_active",
        "legal_name": "SYNGENTA INDIA PRIVATE LIMITED",
        "trade_name": "Syngenta India",
        "registration_type": "Regular",
        "taxpayer_status": "Active",
        "effective_from": "2017-07-01",
        "principal_address": "Amar Paradigm, Baner Road, Pune",
        "state_code": "09",
    },
    # The two issuing entities, so a self-check works offline.
    "07AAICT8535C1Z9": {
        "status": "valid_active",
        "legal_name": "THETA FOUNDATION FOR DEVELOPMENT",
        "registration_type": "Regular",
        "taxpayer_status": "Active",
        "effective_from": "2021-04-01",
        "principal_address": "L-20 Lower Basement, Green Park, New Delhi 110016",
        "state_code": "07",
    },
    "07AAHCT0066D1ZM": {
        "status": "valid_active",
        "legal_name": "THETA ENERLYTICS PRIVATE LIMITED",
        "registration_type": "Regular",
        "taxpayer_status": "Active",
        "effective_from": "2022-06-01",
        "principal_address": "A 10/3 Front Ground Floor, Vasant Vihar, New Delhi 110057",
        "state_code": "07",
    },
    # 🔴 Constructed hard cases. Each maps to a pre-issue check that must fire,
    # and each carries a *real* check digit — the local layer rejects a
    # malformed GSTIN before any lookup happens, so a fixture with a made-up
    # 15th character would exercise the format check and never reach the
    # provider path it was written to test.
    "27AAAAA0000A1Z2": {
        "status": "cancelled",
        "legal_name": "CANCELLED TRADER PRIVATE LIMITED",
        "taxpayer_status": "Cancelled",
        "effective_from": "2018-04-01",
        "cancellation_date": "2025-03-31",
        "state_code": "27",
    },
    "29BBBBB1111B1ZJ": {
        "status": "valid_inactive",
        "legal_name": "SUSPENDED SUPPLIES LLP",
        "taxpayer_status": "Suspended",
        "effective_from": "2019-01-01",
        "state_code": "29",
    },
    # Registered in Maharashtra, so an invoice recording the buyer in UP is a
    # state conflict that changes the tax treatment.
    "27CCCCC2222C1Z8": {
        "status": "valid_active",
        "legal_name": "STATE MISMATCH TRADERS PRIVATE LIMITED",
        "taxpayer_status": "Active",
        "effective_from": "2020-01-01",
        "state_code": "27",
    },
    # Mizoram's Department of Agriculture bills under a government UIN, which
    # carries no PAN and no check digit.
    "15SHLD02015GIDQ": {
        "status": "valid_active",
        "legal_name": "DIRECTOR, DEPARTMENT OF AGRICULTURE (CROP HUSBANDRY), MIZORAM",
        "registration_type": "Government Department / UIN",
        "taxpayer_status": "Active",
        "effective_from": "2017-07-01",
        "state_code": "15",
    },
}

#: A GSTIN whose lookup always reports the provider as unreachable. Its only
#: job is to make the "downtime is not valid" assertion testable.
OUTAGE_GSTIN = "33DDDDD3333D1Z0"


class FakeGstinLookupProvider:
    """
    Offline, deterministic, and honest about what it does not know.

    A checksum-valid GSTIN absent from the fixtures returns `not_found` rather
    than a plausible company — inventing an identity here would be inventing
    statutory data, which is exactly what the brief forbids.
    """

    name = "fake"

    async def lookup(self, gstin: str) -> LookupResult:
        cleaned = (gstin or "").strip().upper()

        if cleaned == OUTAGE_GSTIN:
            return LookupResult(
                status="verification_unavailable",
                gstin=cleaned,
                provider=self.name,
                error_code="provider_unreachable",
                error_detail=(
                    "The verification provider did not respond. This GSTIN's status "
                    "is unknown, which is not the same as valid."
                ),
            )

        if not gstin_lib.is_valid(cleaned, allow_govt_uin=True):
            return LookupResult(
                status="invalid_format",
                gstin=cleaned,
                provider=self.name,
                error_code="invalid_format",
                error_detail="Rejected locally; no lookup was made.",
            )

        fixture = FIXTURES.get(cleaned)
        if fixture is None:
            return LookupResult(
                status="not_found",
                gstin=cleaned,
                provider=self.name,
                provider_reference=f"fake-{cleaned[-6:]}",
                error_detail=(
                    "No registration matched. The development provider holds a small "
                    "fixture set rather than the register; a live provider is needed "
                    "for real customers."
                ),
                raw={"source": "fixture", "found": False},
            )

        return LookupResult(
            status=fixture["status"],
            gstin=cleaned,
            provider=self.name,
            provider_reference=f"fake-{cleaned[-6:]}",
            legal_name=fixture.get("legal_name"),
            trade_name=fixture.get("trade_name"),
            registration_type=fixture.get("registration_type"),
            taxpayer_status=fixture.get("taxpayer_status"),
            effective_from=(
                date.fromisoformat(fixture["effective_from"])
                if fixture.get("effective_from")
                else None
            ),
            cancellation_date=(
                date.fromisoformat(fixture["cancellation_date"])
                if fixture.get("cancellation_date")
                else None
            ),
            principal_address=fixture.get("principal_address"),
            state_code=fixture.get("state_code") or cleaned[:2],
            raw={"source": "fixture", "found": True, **fixture},
        )


# ---------------------------------------------------------------------------
# The HTTP shape a real GSP plugs into
# ---------------------------------------------------------------------------


class HttpGstinLookupProvider:
    """
    A generic GSP adapter, disabled until a base URL and key are configured.

    Deliberately thin, and deliberately not named after a vendor: the field
    mapping below is the only vendor-specific part, and a second provider is a
    subclass overriding `_map`, not a fork of the domain.

    🔴 The request carries the GSTIN and nothing else.
    """

    name = "http"

    def __init__(self) -> None:
        from backend.config import settings

        if not settings.gstin_lookup_base_url or not settings.gstin_lookup_api_key:
            raise RuntimeError(
                "GSTIN_LOOKUP_PROVIDER is set to a live provider but "
                "GSTIN_LOOKUP_BASE_URL / GSTIN_LOOKUP_API_KEY are empty. Configure "
                "both, or use GSTIN_LOOKUP_PROVIDER=fake — 🔴 a silent fallback to "
                "the fake would show fixture data as a live verification, which is "
                "worse than no verification at all."
            )
        self._base_url = settings.gstin_lookup_base_url.rstrip("/")
        self._api_key = settings.gstin_lookup_api_key

    async def lookup(self, gstin: str) -> LookupResult:
        import httpx

        cleaned = (gstin or "").strip().upper()
        if not gstin_lib.is_valid(cleaned, allow_govt_uin=True):
            # Never spend a paid lookup on something the local check rejects.
            return LookupResult(
                status="invalid_format",
                gstin=cleaned,
                provider=self.name,
                error_code="invalid_format",
                error_detail="Rejected locally; no lookup was made.",
            )

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{self._base_url}/taxpayer/{cleaned}",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as error:
            # 🔴 The critical branch. Unreachable is unknown, not valid.
            logger.warning("GSTIN lookup failed for %s***: %s", cleaned[:2], error)
            return LookupResult(
                status="verification_unavailable",
                gstin=cleaned,
                provider=self.name,
                error_code="provider_unreachable",
                error_detail=str(error),
            )

        if response.status_code == 404:
            return LookupResult(status="not_found", gstin=cleaned, provider=self.name)
        if response.status_code >= 500:
            return LookupResult(
                status="verification_unavailable",
                gstin=cleaned,
                provider=self.name,
                error_code=f"http_{response.status_code}",
                error_detail="The provider returned a server error.",
            )
        if response.status_code >= 400:
            return LookupResult(
                status="error",
                gstin=cleaned,
                provider=self.name,
                error_code=f"http_{response.status_code}",
                error_detail=response.text[:500],
            )

        try:
            payload = response.json()
        except ValueError:
            return LookupResult(
                status="error",
                gstin=cleaned,
                provider=self.name,
                error_code="unparseable",
                error_detail="The provider's reply was not JSON.",
            )

        return self._map(cleaned, payload)

    def _map(self, gstin: str, payload: dict[str, Any]) -> LookupResult:
        """
        The one vendor-specific method. Override it for a different GSP.

        Field names follow the common GSTN `search` response shape (`sts`,
        `lgnm`, `tradeNam`, `ctb`, `rgdt`, `cxdt`, `pradr`).
        """
        raw_status = str(payload.get("sts") or payload.get("status") or "").lower()
        status = {
            "active": "valid_active",
            "cancelled": "cancelled",
            "suspended": "valid_inactive",
            "inactive": "valid_inactive",
            "provisional": "provisional",
        }.get(raw_status, "error")

        def _date(value: Any) -> date | None:
            if not value:
                return None
            text = str(value)
            for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
                try:
                    # A registration date is a calendar date, so the naive
                    # parse is then reduced to `.date()` immediately — there is
                    # no instant here for a timezone to be wrong about.
                    return datetime.strptime(text, pattern).replace(tzinfo=UTC).date()
                except ValueError:
                    continue
            return None

        address = payload.get("pradr")
        if isinstance(address, dict):
            address = address.get("adr")

        return LookupResult(
            status=status,
            gstin=gstin,
            provider=self.name,
            provider_reference=str(payload.get("requestId") or "") or None,
            legal_name=payload.get("lgnm"),
            trade_name=payload.get("tradeNam"),
            registration_type=payload.get("ctb"),
            taxpayer_status=payload.get("sts"),
            effective_from=_date(payload.get("rgdt")),
            cancellation_date=_date(payload.get("cxdt")),
            principal_address=address if isinstance(address, str) else None,
            state_code=gstin[:2],
            raw=payload,
        )


def get_provider() -> GstinLookupProvider:
    """🔴 An unknown name raises. There is no silent fallback to the fake."""
    from backend.config import settings

    name = (settings.gstin_lookup_provider or "fake").lower()
    if name == "fake":
        return FakeGstinLookupProvider()
    if name in ("http", "gsp"):
        return HttpGstinLookupProvider()
    raise RuntimeError(
        f"GSTIN_LOOKUP_PROVIDER='{name}' is not a provider this build knows. "
        f"Use 'fake', or 'http' with a base URL and key."
    )
