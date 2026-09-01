"""
GSTIN verification — the two layers, and the failure the feature exists to stop.

🔴 The assertion that matters most in this file is
`test_provider_downtime_is_never_reported_as_valid`. Every other property here
is a convenience; that one is the difference between a control and a
decoration, because a verification service that answers "probably fine" when it
cannot reach the registry is worse than none — it produces a confident record
of a check that did not happen.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from backend.providers.gstin_lookup import OUTAGE_GSTIN
from backend.tests.conftest import PASSWORD

pytestmark = pytest.mark.anyio

SYNGENTA_UP = "09AAECS9424P1ZL"
CANCELLED = "27AAAAA0000A1Z2"
SUSPENDED = "29BBBBB1111B1ZJ"
STATE_MISMATCH = "27CCCCC2222C1Z8"
MIZORAM_UIN = "15SHLD02015GIDQ"
#: The defect in the historical data: 14 characters, not 15.
SHORT_GSTIN = "09AAECS942P1ZL"


async def _headers(client, user) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login/", json={"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access']}"}


async def _entity(session):
    from backend.models.billing import BillingEntity

    return await session.scalar(
        select(BillingEntity).where(BillingEntity.valid_to.is_(None)).limit(1)
    )


# ---------------------------------------------------------------------------
# Layer one — local
# ---------------------------------------------------------------------------


async def test_a_short_gstin_is_rejected_with_a_useful_message(client, biller):
    """
    🔴 D1: 29 of 105 historical lines carry a GSTIN one character short.

    "Invalid GSTIN" tells the person typing nothing they can act on; naming the
    length tells them they dropped a digit.
    """
    response = await client.get(
        "/api/v1/gstin/check/",
        params={"value": SHORT_GSTIN},
        headers=await _headers(client, biller),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["valid"] is False
    assert "15 characters" in body["message"]
    assert "14" in body["message"]


async def test_the_local_check_never_claims_verification(client, biller):
    """
    🔴 A checksum-valid GSTIN is well-formed, not active. The response says so,
    and carries no field a UI could render as "GST-verified".
    """
    response = await client.get(
        "/api/v1/gstin/check/",
        params={"value": SYNGENTA_UP},
        headers=await _headers(client, biller),
    )
    body = response.json()

    assert body["valid"] is True
    assert body["normalised"] == SYNGENTA_UP
    assert body["state_code"] == "09"
    assert body["state_name"] == "Uttar Pradesh"
    assert "never label it" in body["note"]
    assert "is_verified" not in body


async def test_a_government_uin_needs_the_flag(client, biller):
    """
    Mizoram's department bills under a UIN with no PAN and no check digit.
    Allowing it needs an explicit flag rather than a weaker regex, or the
    validator stops catching the typos it exists to catch.
    """
    headers = await _headers(client, biller)

    without = await client.get(
        "/api/v1/gstin/check/", params={"value": MIZORAM_UIN}, headers=headers
    )
    assert without.json()["valid"] is False
    assert "government UIN" in without.json()["message"]

    with_flag = await client.get(
        "/api/v1/gstin/check/",
        params={"value": MIZORAM_UIN, "govt_uin": True},
        headers=headers,
    )
    assert with_flag.json()["valid"] is True
    assert with_flag.json()["is_govt_uin"] is True


# ---------------------------------------------------------------------------
# Layer two — live
# ---------------------------------------------------------------------------


async def test_an_active_registration_verifies(client, biller, session):
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    response = await client.post(
        "/api/v1/gstin/verifications/",
        json={"billing_entity": str(entity.id), "gstin": SYNGENTA_UP},
        headers=await _headers(client, biller),
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "valid_active"
    assert body["is_verified"] is True
    assert body["is_unavailable"] is False
    assert "SYNGENTA" in body["legal_name"]
    assert body["raw_response_sha256"], "the provider's reply was not hashed for audit"


async def test_provider_downtime_is_never_reported_as_valid(client, biller, session):
    """
    🔴 The assertion this whole feature exists for (INVOICE.md §12.4).

    A provider that cannot be reached leaves the registration's status unknown.
    Unknown is not valid, it is not "assume fine", and it is not folded into
    "not verified" either — it has its own state, its own label, and it never
    gets cached.
    """
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    response = await client.post(
        "/api/v1/gstin/verifications/",
        json={"billing_entity": str(entity.id), "gstin": OUTAGE_GSTIN},
        headers=await _headers(client, biller),
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "verification_unavailable"
    assert body["is_verified"] is False
    assert body["is_unavailable"] is True
    assert "not the same as valid" in body["label"]
    # 🔴 No TTL: reusing "we could not reach the provider" would turn one
    # outage into a permanent unknown.
    assert body["expires_at"] is None


async def test_a_cancelled_registration_is_reported_as_cancelled(client, biller, session):
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    body = (
        await client.post(
            "/api/v1/gstin/verifications/",
            json={"billing_entity": str(entity.id), "gstin": CANCELLED},
            headers=await _headers(client, biller),
        )
    ).json()

    assert body["status"] == "cancelled"
    assert body["is_verified"] is False
    assert body["cancellation_date"] == "2025-03-31"


async def test_a_malformed_gstin_costs_no_lookup(client, biller, session):
    """
    Rejected locally, recorded, and no request made. A paid lookup spent on
    something the checksum already refused is money for nothing.
    """
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    body = (
        await client.post(
            "/api/v1/gstin/verifications/",
            json={"billing_entity": str(entity.id), "gstin": SHORT_GSTIN},
            headers=await _headers(client, biller),
        )
    ).json()

    assert body["status"] == "invalid_format"
    assert body["error_code"] == "invalid_format"
    # The local message is carried through rather than replaced by a generic
    # one: "this has 14" tells the person typing they dropped a digit.
    assert "15 characters" in body["error_detail"]
    # No provider was consulted, so there is no reference and nothing to cache.
    assert body["provider_reference"] is None
    assert body["expires_at"] is None


async def test_a_second_lookup_is_served_from_cache(client, biller, session):
    """Cached for the TTL, so opening an invoice twice costs one lookup."""
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    payload = {"billing_entity": str(entity.id), "gstin": SYNGENTA_UP}

    first = (
        await client.post("/api/v1/gstin/verifications/", json=payload, headers=headers)
    ).json()
    second = (
        await client.post("/api/v1/gstin/verifications/", json=payload, headers=headers)
    ).json()

    assert second["id"] == first["id"], "the second lookup was not served from cache"

    forced = (
        await client.post(
            "/api/v1/gstin/verifications/",
            json={**payload, "force": True},
            headers=headers,
        )
    ).json()
    assert forced["id"] != first["id"], "`force` did not bypass the cache"


# ---------------------------------------------------------------------------
# Invoice evidence and issue enforcement
# ---------------------------------------------------------------------------


async def _draft(client, headers, entity_id, **overrides):
    payload = {
        "billing_entity": str(entity_id),
        "invoice_date": date.today().isoformat(),
        "buyer_name": "GSTIN Test Buyer [api-test]",
        "buyer_gstin": SYNGENTA_UP,
        "buyer_state_code": "09",
        "tax_treatment": "igst",
        "lines": [
            {
                "description": "Drone spraying services",
                "quantity": "10",
                "unit": "acre",
                "rate": "150",
            }
        ],
    }
    payload.update(overrides)
    response = await client.post("/api/v1/invoices/", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def test_the_invoice_check_returns_both_layers(client, biller, session):
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    draft = await _draft(client, headers, entity.id)

    response = await client.post(f"/api/v1/invoices/{draft['id']}/gstin-check/", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["local"]["valid"] is True
    assert body["live"]["is_verified"] is True
    assert body["blocks_issue"] is False


async def test_a_cancelled_registration_blocks_issue(client, biller, session):
    """
    🔴 The I-9 exit gate: an inactive registration is stopped before issue.

    Billing GST to a cancelled registration denies the customer input credit
    and raises a mismatch on their return — their problem before it is yours,
    which is the worst kind.
    """
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    draft = await _draft(client, headers, entity.id, buyer_gstin=CANCELLED, buyer_state_code="27")

    await client.post(f"/api/v1/invoices/{draft['id']}/gstin-check/", headers=headers)

    issued = await client.post(f"/api/v1/invoices/{draft['id']}/issue/", json={}, headers=headers)
    assert issued.status_code == 409, issued.text
    blocking = issued.json()["error"]["details"]["blocking"]
    assert any(item["code"] == "gstin_not_active" for item in blocking)


async def test_a_state_mismatch_blocks_issue(client, biller, session):
    """A registry state that differs from the invoice's changes whether the
    supply is inter-state, so it is a block rather than a warning."""
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    # The GSTIN begins 27; the invoice records the buyer in 27 so the local
    # consistency check passes, and the registry then disagrees on nothing —
    # so this uses a buyer state the GSTIN itself contradicts.
    draft = await _draft(
        client, headers, entity.id, buyer_gstin=STATE_MISMATCH, buyer_state_code="09"
    )

    issued = await client.post(f"/api/v1/invoices/{draft['id']}/issue/", json={}, headers=headers)
    assert issued.status_code == 409, issued.text
    codes = {item["code"] for item in issued.json()["error"]["details"]["blocking"]}
    assert "buyer_state_conflict" in codes or "gstin_state_mismatch" in codes


async def test_use_verified_details_needs_a_draft(client, biller, session):
    """
    🔴 An issued invoice's buyer block is a snapshot of what was printed. A
    customer that moved office must not silently alter a document their
    accounts team already holds.
    """
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    draft = await _draft(client, headers, entity.id)

    applied = await client.post(
        f"/api/v1/invoices/{draft['id']}/gstin-check/use-verified/", headers=headers
    )
    assert applied.status_code == 200, applied.text
    changes = {item["field"] for item in applied.json()["changes"]}
    assert "buyer_name" in changes

    issued = await client.post(f"/api/v1/invoices/{draft['id']}/issue/", json={}, headers=headers)
    assert issued.status_code == 200, issued.text

    after = await client.post(
        f"/api/v1/invoices/{draft['id']}/gstin-check/use-verified/", headers=headers
    )
    assert after.status_code == 400
    assert "never rewritten" in after.text


async def test_an_override_cannot_excuse_a_cancelled_registration(client, biller, session):
    """
    🔴 An override is for an *unknown*, not for a known-bad.

    "The provider was unreachable and the work cannot wait" is a judgement a
    named person can make. "The registry says this registration is cancelled"
    is not.
    """
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    draft = await _draft(client, headers, entity.id, buyer_gstin=CANCELLED, buyer_state_code="27")

    response = await client.post(
        f"/api/v1/invoices/{draft['id']}/gstin-check/override/",
        json={"reason": "The customer assures us the registration is being restored."},
        headers=headers,
    )
    # data_ops / compliance / admin may override; project_manager may not, and
    # either refusal is correct here. What must never happen is a 200.
    assert response.status_code in (400, 403), response.text
    assert response.status_code != 200


async def test_an_override_of_an_outage_records_actor_and_reason(
    client, data_ops, session, mfa_headers
):
    """
    The permitted case, and what it must capture.

    🔴 Actor, reason and time, on a row a trigger refuses to rewrite. An
    override nobody can review afterwards is a bypass, not a control.
    """
    from backend.models.invoice_ops import InvoiceGstinCheck

    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await mfa_headers(data_ops)
    draft = await _draft(
        client, headers, entity.id, buyer_gstin=OUTAGE_GSTIN, buyer_state_code="33"
    )

    response = await client.post(
        f"/api/v1/invoices/{draft['id']}/gstin-check/override/",
        json={"reason": "Provider outage confirmed with the GSP; work is time-critical."},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["overridden"] is True
    assert response.json()["blocks_issue"] is False

    row = await session.scalar(
        select(InvoiceGstinCheck).where(InvoiceGstinCheck.invoice_id == uuid.UUID(draft["id"]))
    )
    assert row is not None
    assert row.override_by == data_ops.public_id
    assert "Provider outage" in row.override_reason
    assert row.override_at is not None
