"""
Compliance intelligence — dated knowledge and the accounting handoff.

🔴 The I-10 exit gate names two things this file holds:

* every tax/code suggestion shows its effective date and source
* nothing claims to file a return or obtain an IRN

The second is asserted structurally as well as behaviourally — a grep over the
export module for filing language, because the risk is not that somebody writes
`file_gstr1()` on purpose, it is that a helpful sentence in a docstring or a
response field gradually turns a working paper into something a user believes
was submitted.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.tests.conftest import PASSWORD

pytestmark = pytest.mark.anyio


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


@pytest.fixture
async def seeded(session, biller):
    """The two SACs this business bills under, seeded `under_review`."""
    from backend.domain import knowledge

    await knowledge.seed_records(session, created_by=biller.public_id)
    return True


# ---------------------------------------------------------------------------
# Dated knowledge
# ---------------------------------------------------------------------------


async def test_a_suggestion_carries_its_effective_date_and_source(client, biller, session, seeded):
    """🔴 The I-10 exit gate. A code with no citation is an opinion."""
    response = await client.get(
        "/api/v1/tax-codes/suggest/",
        params={"description": "Drone spraying of standing cane"},
        headers=await _headers(client, biller),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["code"] == "998611"
    assert body["effective_from"] == "2017-07-01"
    assert body["citation"]["title"]
    assert "Notification" in body["citation"]["title"]


async def test_an_unreviewed_record_is_never_presented_as_verified(client, biller, session, seeded):
    """
    🔴 A seeded record is `under_review`. Presenting it as a classification
    would be this codebase making a tax determination.
    """
    body = (
        await client.get(
            "/api/v1/tax-codes/suggest/",
            params={"description": "drone survey base map"},
            headers=await _headers(client, biller),
        )
    ).json()

    assert body["code"] == "997319"
    assert body["review_status"] == "under_review"
    assert body["is_verified"] is False
    assert "not reviewed by a CA" in body["label"]


async def test_retrieval_uses_the_invoice_date_not_today(client, biller, session):
    """
    🔴 A rate that changed in July does not apply to a June document.

    Two records for one code with adjoining effective ranges; the lookup for a
    date inside the earlier range must return the earlier record even though
    the later one is current.
    """
    from datetime import UTC, datetime

    from backend.domain import knowledge
    from backend.models.invoice_ops import TaxCodeKnowledge

    code = f"TEST{uuid.uuid4().hex[:4].upper()}"
    session.add(
        TaxCodeKnowledge(
            code=code,
            description="Test service, original rate",
            gst_rate_pct=Decimal("12.00"),
            effective_from=date(2024, 4, 1),
            effective_to=date(2025, 6, 30),
            source_title="Fixture — original notification",
            review_status="under_review",
            keywords=["fixture service"],
            created_at=datetime.now(UTC),
        )
    )
    session.add(
        TaxCodeKnowledge(
            code=code,
            description="Test service, revised rate",
            gst_rate_pct=Decimal("18.00"),
            effective_from=date(2025, 7, 1),
            source_title="Fixture — revising notification",
            review_status="under_review",
            keywords=["fixture service"],
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()

    earlier = await knowledge.effective_on(session, code=code, on_date=date(2025, 5, 15))
    later = await knowledge.effective_on(session, code=code, on_date=date(2026, 1, 15))

    assert earlier is not None and earlier.gst_rate_pct == Decimal("12.00")
    assert later is not None and later.gst_rate_pct == Decimal("18.00")


async def test_approval_needs_a_named_reviewer(client, data_ops, session, seeded, mfa_headers):
    """
    🔴 The only route to `is_verified`, and it records who.

    The database refuses `approved` without a reviewer, so an approval cannot
    exist without somebody's name against it.
    """
    from backend.models.invoice_ops import TaxCodeKnowledge

    row = await session.scalar(
        select(TaxCodeKnowledge).where(TaxCodeKnowledge.code == "998611").limit(1)
    )
    headers = await mfa_headers(data_ops)

    blank = await client.post(
        f"/api/v1/tax-codes/{row.id}/approve/",
        json={"reviewer_name": " "},
        headers=headers,
    )
    assert blank.status_code == 400

    approved = await client.post(
        f"/api/v1/tax-codes/{row.id}/approve/",
        json={"reviewer_name": "R. Sharma, Chartered Accountant"},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["is_verified"] is True
    assert body["citation"]["reviewer"] == "R. Sharma, Chartered Accountant"
    assert body["citation"]["reviewed_at"] is not None


async def test_a_biller_cannot_approve_a_tax_code(client, biller, session, seeded):
    """Approval is compliance's, not billing's."""
    from backend.models.invoice_ops import TaxCodeKnowledge

    row = await session.scalar(
        select(TaxCodeKnowledge).where(TaxCodeKnowledge.code == "998611").limit(1)
    )
    response = await client.post(
        f"/api/v1/tax-codes/{row.id}/approve/",
        json={"reviewer_name": "Somebody Else"},
        headers=await _headers(client, biller),
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


async def _issued(client, headers, entity_id, **overrides):
    payload = {
        "billing_entity": str(entity_id),
        "invoice_date": date.today().isoformat(),
        "buyer_name": "Export Test Buyer [api-test]",
        "buyer_gstin": "09AAECS9424P1ZL",
        "buyer_state_code": "09",
        "tax_treatment": "igst",
        "lines": [
            {
                "description": "Drone spraying services",
                "hsn_sac": "998611",
                "quantity": "215",
                "unit": "acre",
                "rate": "150",
            }
        ],
    }
    payload.update(overrides)
    created = await client.post("/api/v1/invoices/", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    invoice = created.json()
    issued = await client.post(f"/api/v1/invoices/{invoice['id']}/issue/", json={}, headers=headers)
    assert issued.status_code == 200, issued.text
    return invoice


async def test_the_tally_export_is_line_level_and_carries_the_hsn(client, biller, session):
    """
    Line-level rather than invoice-level: an aggregated row loses the HSN, and
    that is the column the GST returns are built from.
    """
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    await _issued(client, headers, entity.id)

    response = await client.get(
        "/api/v1/exports/tally.csv",
        params={"date_from": date.today().isoformat()},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "not a filing" in response.headers["x-export-disclaimer"].lower()

    body = response.text
    assert "HSN/SAC" in body
    assert "998611" in body
    assert "Drone spraying services" in body


async def test_the_zoho_export_maps_the_gst_treatment(client, biller, session):
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    await _issued(client, headers, entity.id)

    body = (
        await client.get(
            "/api/v1/exports/zoho.csv",
            params={"date_from": date.today().isoformat()},
            headers=headers,
        )
    ).text

    assert "GST Treatment" in body
    assert "business_gst" in body


async def test_the_gstr1_sheet_is_labelled_a_working_paper(client, biller, session):
    """🔴 The I-10 exit gate: nothing claims to file a return."""
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    await _issued(client, headers, entity.id)

    response = await client.get(
        "/api/v1/exports/gstr1-working-paper/",
        params={
            "date_from": (date.today() - timedelta(days=1)).isoformat(),
            "date_to": date.today().isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert "not a filing" in body["disclaimer"].lower()
    assert "has been or will be submitted" in body["not_a_filing"]
    assert "no IRN has been obtained" in body["not_a_filing"]
    assert body["b2b"], "the issued invoice is missing from the B2B table"


async def test_an_invoice_without_an_hsn_raises_a_warning(client, biller, session):
    """
    The valuable half of the sheet: the rows a portal would reject, found here
    instead.
    """
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    invoice = await _issued(
        client,
        headers,
        entity.id,
        lines=[
            {
                "description": "Drone spraying services",
                "quantity": "100",
                "unit": "acre",
                "rate": "150",
            }
        ],
    )

    body = (
        await client.get(
            "/api/v1/exports/gstr1-working-paper/",
            params={
                "date_from": (date.today() - timedelta(days=1)).isoformat(),
                "date_to": date.today().isoformat(),
            },
            headers=headers,
        )
    ).json()

    matching = [
        w for w in body["warnings"] if w["code"] == "no_hsn" and w["invoice_id"] == invoice["id"]
    ]
    assert matching, "an invoice with no HSN did not raise a warning"


async def test_nothing_in_the_export_module_claims_to_file(client):
    """
    🔴 Structural, and deliberately blunt.

    The risk is not that somebody writes `submit_to_portal()` on purpose. It is
    that a helpful sentence gradually turns a working paper into something a
    user believes was filed. This fails on the language.
    """
    import inspect
    import re

    from backend.domain import exports

    source = inspect.getsource(exports).lower()

    # 🔴 Affirmative claims only. The disclaimer itself says "Nothing in this
    # system files a return, obtains an IRN, or posts to a ledger" — a bare
    # substring search flags that denial, which would make the guard fire on
    # the very sentence it exists to protect. So each pattern requires a
    # subject that is *this system* doing the filing.
    claims = (
        r"\bthis (?:module|export|sheet|file|system) (?:files|submits|uploads)\b",
        r"\bwe (?:file|submit|upload) (?:the |your )?(?:return|gstr)",
        r"\bfiling[- ]ready\b",
        r"\bready to (?:file|submit|upload)\b",
        r"\b(?:generates|obtains|fetches) (?:an |the )?irn\b(?!,)",
        r"\bsubmits? (?:it |this )?to the portal\b",
    )
    for pattern in claims:
        match = re.search(pattern, source)
        assert match is None, (
            f"the export module claims to file: {match.group(0)!r}. "
            f"This module produces working papers; filing is the CA's, in Tally."
        )

    # And the denial must actually be present — a guard that only forbids
    # language would pass on a module that says nothing at all.
    # 🔴 The guard is checked against a claim it must catch. A pattern set
    # that matches nothing passes every module, including a bad one — which
    # is exactly what happened when these escapes were briefly mangled into
    # literal backspace characters and this test went on passing.
    assert re.search(claims[2], "this export is filing-ready") is not None

    assert "not a filing" in exports.DISCLAIMER.lower()
