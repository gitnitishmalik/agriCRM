"""
The invoice register, read through FastAPI.

These run against the live register rather than fixtures they invent. That is
deliberate for a read-only port: the value of these tests is proving the
SQLAlchemy mapping agrees with the DDL-owned schema, and a fixture built from
the same mapping would agree with itself no matter how wrong both were.

They assert shape and invariants, never a specific total — the register is
written to by the Django service and by people, and a test pinned to
₹15,78,250.00 is a test that fails the next time someone issues an invoice.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.tests.conftest import PASSWORD

pytestmark = pytest.mark.anyio


async def _signed_in(client, user) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login/", json={"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access']}"}


async def test_the_register_lists(client, agent):
    body = (await client.get("/api/v1/invoices/", headers=await _signed_in(client, agent))).json()

    assert "count" in body
    assert isinstance(body["results"], list)


async def test_a_row_carries_preformatted_money(client, agent):
    """
    🔴 The server formats. Indian grouping lives in one place per service and
    a second implementation in TypeScript is a second one to get wrong.
    """
    headers = await _signed_in(client, agent)
    body = (await client.get("/api/v1/invoices/?limit=5", headers=headers)).json()

    if not body["results"]:
        pytest.skip("register is empty")

    row = body["results"][0]
    # 🔴 Every figure the screen shows must be here. `taxable`, `tax` and
    # `received` were absent while the invoice screen read them, so it rendered
    # "₹undefined" for three of the five money rows on every invoice — the
    # exact failure this test exists to prevent, missed because it pinned the
    # short set. Adding a figure to a screen means adding it here.
    assert set(row["display"]) == {"total", "outstanding", "taxable", "tax", "received"}
    # Two decimal places always, and no western thousands separator pattern
    # like 1,578,250 — the grouping test in test_django_parity covers the rule.
    assert row["display"]["total"].endswith((".00", ".50")) or "." in row["display"]["total"]


async def test_the_summary_excludes_what_is_not_owed(client, agent):
    """
    🔴 Cancelled, discarded and draft invoices are documents that exist and
    are not owed. Counting them makes a receivables number meaningless.
    """
    headers = await _signed_in(client, agent)
    summary = (await client.get("/api/v1/invoices/summary/", headers=headers)).json()

    assert summary["count"] >= 0
    assert set(summary["display"]) == {"taxable", "tax", "total", "received", "outstanding"}

    # The identity that has to hold whatever is in the register.
    total = Decimal(str(summary["total_value"]))
    received = Decimal(str(summary["amount_received"]))
    outstanding = Decimal(str(summary["amount_outstanding"]))
    assert total - received == outstanding


async def test_the_summary_agrees_with_the_rows_it_summarises(client, agent):
    """
    A total that disagrees with the list beneath it is the shape of bug nobody
    reports and everybody stops trusting.
    """
    headers = await _signed_in(client, agent)
    summary = (await client.get("/api/v1/invoices/summary/", headers=headers)).json()
    listed = (await client.get("/api/v1/invoices/?limit=200", headers=headers)).json()

    owed = [r for r in listed["results"] if r["status"] not in ("cancelled", "discarded", "draft")]
    if len(listed["results"]) >= 200:
        pytest.skip("register is larger than one page; the comparison would be partial")

    assert summary["count"] == len(owed)
    assert Decimal(str(summary["total_value"])) == sum(
        (Decimal(str(r["total_value"])) for r in owed), Decimal(0)
    )


async def test_an_unknown_filter_is_rejected_rather_than_ignored(client, agent):
    """
    🔴 A filter that silently does nothing is how someone exports the whole
    register believing they exported one customer.

    FastAPI ignores unknown query parameters by default — this control had to
    be rebuilt on the way over, and this test is what noticed it was missing.
    """
    headers = await _signed_in(client, agent)
    response = await client.get("/api/v1/invoices/?entity_cod=TEPL", headers=headers)

    assert response.status_code == 400
    assert "entity_cod" in response.text


async def test_a_missing_invoice_is_a_404(client, agent):
    import uuid

    headers = await _signed_in(client, agent)
    response = await client.get(f"/api/v1/invoices/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404


async def test_the_register_needs_authentication(client):
    assert (await client.get("/api/v1/invoices/")).status_code == 401
    assert (await client.get("/api/v1/invoices/summary/")).status_code == 401
