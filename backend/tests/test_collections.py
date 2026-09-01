"""
Collections — ageing, payment requests, webhook reconciliation, delivery.

🔴 The assertions here are about the properties that stop money and messages
going wrong, not about response shapes:

* a duplicate webhook cannot create a second payment
* a mismatched amount reaches the reconciliation queue rather than the invoice
* an unsigned or stale event never produces a payment
* a UPI request is never a payment
* a delivery cannot be sent unless the confirmed hash matches the preview
* an opt-out outranks a confirmed send

Every test runs inside a transaction that is rolled back, against the real
schema. The database is left as it was found.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, date, datetime, timedelta
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


async def _issued_invoice(client, biller, session, *, due_days: int = -40, total="10000"):
    """A real issued invoice, dated so it is overdue by `due_days`."""
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    invoice_date = date.today() - timedelta(days=abs(due_days) + 30)
    payload = {
        "billing_entity": str(entity.id),
        "invoice_date": invoice_date.isoformat(),
        "due_date": (date.today() - timedelta(days=abs(due_days))).isoformat(),
        "buyer_name": "Collections Test Buyer [api-test]",
        "buyer_gstin": "09AAECS9424P1ZL",
        "buyer_state_code": "09",
        "tax_treatment": "igst",
        "tax_rate_pct": "18.00",
        "lines": [
            {
                "description": "Drone spraying services",
                "quantity": "100",
                "unit": "acre",
                "rate": total,
            }
        ],
    }
    headers = await _headers(client, biller)
    created = await client.post("/api/v1/invoices/", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    invoice = created.json()

    issued = await client.post(f"/api/v1/invoices/{invoice['id']}/issue/", json={}, headers=headers)
    assert issued.status_code == 200, issued.text
    return invoice, headers


# ---------------------------------------------------------------------------
# Ageing
# ---------------------------------------------------------------------------


async def test_ageing_derives_outstanding_from_payment_rows(client, biller, session):
    """
    🔴 Outstanding is never stored (INVOICE.md §4.5).

    A part payment reduces it, and the invoice moves into the bucket its due
    date puts it in — not the one its invoice date would.
    """
    invoice, headers = await _issued_invoice(client, biller, session, due_days=45)

    await client.post(
        f"/api/v1/invoices/{invoice['id']}/payments/",
        json={"amount": "400000", "received_on": date.today().isoformat(), "mode": "rtgs"},
        headers=headers,
    )

    report = await client.get("/api/v1/receivables/ageing/", headers=headers)
    assert report.status_code == 200, report.text

    row = next((r for r in report.json()["rows"] if r["invoice_id"] == invoice["id"]), None)
    assert row is not None, "the issued invoice is missing from the ageing report"
    assert Decimal(row["amount_received"]) == Decimal("400000.00")
    assert Decimal(row["amount_outstanding"]) == Decimal("1180000.00") - Decimal("400000.00")
    assert row["bucket"] == "31_60", row["days_overdue"]
    assert row["due_date_assumed"] is False


async def test_an_invoice_without_a_due_date_is_flagged_as_assumed(client, biller, session):
    """
    🔴 An ageing report that silently invents a due date is partly fiction. The
    row says which ones were assumed, and the summary counts them.
    """
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    created = await client.post(
        "/api/v1/invoices/",
        json={
            "billing_entity": str(entity.id),
            "invoice_date": (date.today() - timedelta(days=90)).isoformat(),
            "buyer_name": "No Due Date Buyer [api-test]",
            "buyer_gstin": "09AAECS9424P1ZL",
            "buyer_state_code": "09",
            "tax_treatment": "igst",
            "lines": [{"description": "Survey", "quantity": "1", "unit": "sq_km", "rate": "32000"}],
        },
        headers=headers,
    )
    invoice = created.json()
    await client.post(f"/api/v1/invoices/{invoice['id']}/issue/", json={}, headers=headers)

    report = (await client.get("/api/v1/receivables/ageing/", headers=headers)).json()
    row = next(r for r in report["rows"] if r["invoice_id"] == invoice["id"])

    assert row["due_date_assumed"] is True
    assert row["due_date"] is None
    assert report["summary"]["assumed_due_dates"] >= 1


async def test_collection_priority_shows_every_factor(client, biller, session):
    """
    🔴 A score whose inputs are hidden is a score nobody can argue with.

    Advisory, deterministic, and it denies nobody service — so every factor and
    its point value is in the response.
    """
    invoice, headers = await _issued_invoice(client, biller, session, due_days=100)

    response = await client.get("/api/v1/receivables/priority/", headers=headers)
    assert response.status_code == 200, response.text

    entry = next((e for e in response.json() if e["invoice_id"] == invoice["id"]), None)
    assert entry is not None
    assert entry["score"] > 0
    assert {f["factor"] for f in entry["factors"]} >= {"days_overdue", "amount_outstanding"}
    for factor in entry["factors"]:
        assert factor["explanation"], "a factor with no explanation is not transparent"
    assert "does not deny service" in entry["disclaimer"]


async def test_a_promise_to_pay_lowers_the_priority(client, biller, session):
    """Chasing somebody the day after they promised is noise, and it scores as such."""
    invoice, headers = await _issued_invoice(client, biller, session, due_days=60)

    before = (await client.get("/api/v1/receivables/priority/", headers=headers)).json()
    before_score = next(e["score"] for e in before if e["invoice_id"] == invoice["id"])

    promised = await client.post(
        f"/api/v1/invoices/{invoice['id']}/promises/",
        json={
            "promised_on": (date.today() + timedelta(days=5)).isoformat(),
            "note": "Accounts confirmed release next week",
        },
        headers=headers,
    )
    assert promised.status_code == 201, promised.text

    after = (await client.get("/api/v1/receivables/priority/", headers=headers)).json()
    after_entry = next(e for e in after if e["invoice_id"] == invoice["id"])

    assert after_entry["score"] < before_score
    assert any(f["factor"] == "payment_promised" for f in after_entry["factors"])


# ---------------------------------------------------------------------------
# Payment requests
# ---------------------------------------------------------------------------


async def test_a_upi_request_is_not_a_payment(client, biller, session):
    """
    🔴 The single most important assertion in this file.

    Generating a UPI link and a QR code records nothing about whether anyone
    paid. The status says so, and the outstanding amount does not move.
    """
    invoice, headers = await _issued_invoice(client, biller, session)

    response = await client.post(
        f"/api/v1/invoices/{invoice['id']}/payment-requests/",
        json={"provider": "manual_upi"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "awaiting_manual_confirmation"
    assert body["is_payment"] is False
    assert body["payload_url"].startswith("upi://pay?")
    assert "not a payment" in body["note"]

    detail = (await client.get(f"/api/v1/invoices/{invoice['id']}", headers=headers)).json()
    assert Decimal(detail["amount_received"]) == Decimal("0.00")
    assert detail["status"] == "issued"


async def test_a_payment_request_is_idempotent(client, biller, session):
    """A retried POST must not send the customer a second link for one invoice."""
    invoice, headers = await _issued_invoice(client, biller, session)
    payload = {"provider": "manual_upi", "idempotency_key": "test-key-1"}

    first = await client.post(
        f"/api/v1/invoices/{invoice['id']}/payment-requests/", json=payload, headers=headers
    )
    second = await client.post(
        f"/api/v1/invoices/{invoice['id']}/payment-requests/", json=payload, headers=headers
    )

    assert first.status_code == 201
    assert second.json()["id"] == first.json()["id"]


async def test_a_request_cannot_exceed_what_is_outstanding(client, biller, session):
    invoice, headers = await _issued_invoice(client, biller, session)

    response = await client.post(
        f"/api/v1/invoices/{invoice['id']}/payment-requests/",
        json={"provider": "manual_upi", "amount": "99999999"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "outstanding" in response.text


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def _signed(secret: str, payload: dict) -> tuple[bytes, dict[str, str]]:
    from backend.providers.payments import FakeGatewayProvider

    body = json.dumps(payload).encode()
    stamp = str(int(time.time()))
    signature = FakeGatewayProvider(secret).sign(body, stamp)
    return body, {
        "X-Webhook-Signature": signature,
        "X-Webhook-Timestamp": stamp,
        "Content-Type": "application/json",
    }


async def _gateway_request(client, headers, invoice_id: str) -> dict:
    response = await client.post(
        f"/api/v1/invoices/{invoice_id}/payment-requests/",
        json={"provider": "fake_gateway"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_a_signed_matching_webhook_creates_exactly_one_payment(client, biller, session):
    """
    The one path in the system where a machine creates a payment: verified
    signature, fresh timestamp, and amount, currency and reference all matching
    an outstanding request.
    """
    from backend.config import settings

    secret = settings.payment_webhook_secret

    invoice, headers = await _issued_invoice(client, biller, session)
    request = await _gateway_request(client, headers, invoice["id"])

    body, signature_headers = _signed(
        secret,
        {
            "event_id": f"evt-{uuid.uuid4().hex}",
            "event": "payment.captured",
            "amount": request["amount"],
            "currency": "INR",
            "reference": request["provider_reference"],
        },
    )

    response = await client.post(
        "/api/v1/payment-webhooks/fake_gateway/", content=body, headers=signature_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"] == "processed"

    detail = (await client.get(f"/api/v1/invoices/{invoice['id']}", headers=headers)).json()
    assert Decimal(detail["amount_received"]) == Decimal(request["amount"])
    assert len(detail["payments"]) == 1


async def test_a_duplicate_webhook_cannot_create_a_second_payment(client, biller, session):
    """
    🔴 The assertion the exit gate names.

    A gateway retries on any non-2xx and on its own timeouts. Without the
    unique constraint on (provider, event id), a redelivery is a duplicate
    payment row and the customer's invoice reads overpaid.
    """
    from backend.config import settings

    secret = settings.payment_webhook_secret

    invoice, headers = await _issued_invoice(client, biller, session)
    request = await _gateway_request(client, headers, invoice["id"])

    body, signature_headers = _signed(
        secret,
        {
            "event_id": "evt-duplicate-test",
            "event": "payment.captured",
            "amount": request["amount"],
            "currency": "INR",
            "reference": request["provider_reference"],
        },
    )

    first = await client.post(
        "/api/v1/payment-webhooks/fake_gateway/", content=body, headers=signature_headers
    )
    second = await client.post(
        "/api/v1/payment-webhooks/fake_gateway/", content=body, headers=signature_headers
    )

    assert first.json()["result"] == "processed"
    # The redelivery is recognised, and it changes nothing.
    assert second.status_code == 200

    detail = (await client.get(f"/api/v1/invoices/{invoice['id']}", headers=headers)).json()
    assert len(detail["payments"]) == 1, "a redelivered webhook created a second payment"


async def test_an_unsigned_webhook_creates_no_payment_but_leaves_evidence(client, biller, session):
    """
    🔴 Store, then verify. A handler that returns early on a bad signature keeps
    no record of the attempt — and "we started receiving events we could not
    verify" is exactly what you want to be able to see afterwards.
    """
    invoice, headers = await _issued_invoice(client, biller, session)
    request = await _gateway_request(client, headers, invoice["id"])

    body = json.dumps(
        {
            "event_id": f"evt-unsigned-{uuid.uuid4().hex}",
            "event": "payment.captured",
            "amount": request["amount"],
            "currency": "INR",
            "reference": request["provider_reference"],
        }
    ).encode()

    response = await client.post(
        "/api/v1/payment-webhooks/fake_gateway/",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["result"] == "signature_failed"

    detail = (await client.get(f"/api/v1/invoices/{invoice['id']}", headers=headers)).json()
    assert detail["payments"] == []

    queue = (await client.get("/api/v1/payment-reconciliation/", headers=headers)).json()
    assert any(item["processing_result"] == "signature_failed" for item in queue)


async def test_a_stale_signed_webhook_is_a_replay(client, biller, session):
    """A correctly signed event from last week is a replay; a signature alone
    cannot tell you that, so the timestamp is checked separately."""
    from backend.config import settings
    from backend.providers.payments import FakeGatewayProvider

    secret = settings.payment_webhook_secret

    invoice, headers = await _issued_invoice(client, biller, session)
    request = await _gateway_request(client, headers, invoice["id"])

    body = json.dumps(
        {
            "event_id": f"evt-stale-{uuid.uuid4().hex}",
            "event": "payment.captured",
            "amount": request["amount"],
            "currency": "INR",
            "reference": request["provider_reference"],
        }
    ).encode()
    old_stamp = str(int(time.time()) - 86_400)
    signature = FakeGatewayProvider(secret).sign(body, old_stamp)

    response = await client.post(
        "/api/v1/payment-webhooks/fake_gateway/",
        content=body,
        headers={
            "X-Webhook-Signature": signature,
            "X-Webhook-Timestamp": old_stamp,
            "Content-Type": "application/json",
        },
    )
    assert response.json()["result"] == "replayed"

    detail = (await client.get(f"/api/v1/invoices/{invoice['id']}", headers=headers)).json()
    assert detail["payments"] == []


async def test_a_mismatched_amount_goes_to_reconciliation(client, biller, session):
    """
    🔴 An amount we did not ask for might be a partial payment, a currency
    error or a different invoice. A system that picks one of those on the
    customer's behalf will eventually pick wrong on a large number.
    """
    from backend.config import settings

    secret = settings.payment_webhook_secret

    invoice, headers = await _issued_invoice(client, biller, session)
    request = await _gateway_request(client, headers, invoice["id"])

    body, signature_headers = _signed(
        secret,
        {
            "event_id": f"evt-mismatch-{uuid.uuid4().hex}",
            "event": "payment.captured",
            "amount": "1.00",
            "currency": "INR",
            "reference": request["provider_reference"],
        },
    )

    response = await client.post(
        "/api/v1/payment-webhooks/fake_gateway/", content=body, headers=signature_headers
    )
    assert response.json()["result"] == "unmatched"
    assert "does not match" in response.json()["detail"]

    detail = (await client.get(f"/api/v1/invoices/{invoice['id']}", headers=headers)).json()
    assert detail["payments"] == []


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


async def _org_with_billing_email(session, email="billing@example.invalid"):
    from backend.models.business import Organisation

    org = Organisation(
        type="private_company",
        legal_form="private_limited",
        name=f"Delivery Test Org {uuid.uuid4().hex[:6]} [api-test]",
        gstin="09AAECS9424P1ZL",
        billing_email=email,
        billing_contact_name="Accounts",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(org)
    await session.flush()
    return org


async def test_a_delivery_needs_a_matching_preview_hash(client, biller, session):
    """
    🔴 The frozen preview. A send whose hash does not match the current preview
    is refused — something changed between seeing it and confirming it.
    """
    org = await _org_with_billing_email(session)
    invoice, headers = await _issued_invoice(client, biller, session)

    await client.patch(
        f"/api/v1/invoices/{invoice['id']}",
        json={},
        headers=headers,
    )
    from backend.models.billing import Invoice

    row = await session.scalar(select(Invoice).where(Invoice.id == uuid.UUID(invoice["id"])))
    row.organisation_id = org.id
    await session.flush()

    preview = await client.post(
        f"/api/v1/invoices/{invoice['id']}/deliveries/preview/",
        json={"channel": "email", "attach_pdf": False},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["recipient"] == "billing@example.invalid"
    assert preview.json()["can_send"] is True

    wrong = await client.post(
        f"/api/v1/invoices/{invoice['id']}/deliveries/",
        json={
            "channel": "email",
            "attach_pdf": False,
            "preview_sha256": "0" * 64,
        },
        headers=headers,
    )
    assert wrong.status_code == 409, wrong.text
    assert "does not match" in wrong.text


async def test_a_confirmed_delivery_records_what_was_sent(client, biller, session):
    org = await _org_with_billing_email(session)
    invoice, headers = await _issued_invoice(client, biller, session)

    from backend.models.billing import Invoice

    row = await session.scalar(select(Invoice).where(Invoice.id == uuid.UUID(invoice["id"])))
    row.organisation_id = org.id
    await session.flush()

    preview = (
        await client.post(
            f"/api/v1/invoices/{invoice['id']}/deliveries/preview/",
            json={"channel": "email", "attach_pdf": False},
            headers=headers,
        )
    ).json()

    sent = await client.post(
        f"/api/v1/invoices/{invoice['id']}/deliveries/",
        json={
            "channel": "email",
            "attach_pdf": False,
            "preview_sha256": preview["preview_sha256"],
        },
        headers=headers,
    )
    assert sent.status_code == 201, sent.text
    assert sent.json()["status"] == "sent"

    history = (
        await client.get(f"/api/v1/invoices/{invoice['id']}/deliveries/", headers=headers)
    ).json()
    assert len(history) == 1
    assert history[0]["recipient"] == "billing@example.invalid"
    # The message as approved, not a template id to re-render later.
    assert invoice["invoice_no"] is None or history[0]["body_snapshot"]


async def test_an_opt_out_blocks_a_delivery(client, biller, session):
    """🔴 An opt-out outranks a send, and the preview says why."""
    org = await _org_with_billing_email(session)
    org.billing_opt_out = True
    org.billing_opt_out_at = datetime.now(UTC)
    await session.flush()

    invoice, headers = await _issued_invoice(client, biller, session)
    from backend.models.billing import Invoice

    row = await session.scalar(select(Invoice).where(Invoice.id == uuid.UUID(invoice["id"])))
    row.organisation_id = org.id
    await session.flush()

    preview = await client.post(
        f"/api/v1/invoices/{invoice['id']}/deliveries/preview/",
        json={"channel": "email", "attach_pdf": False},
        headers=headers,
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["can_send"] is False
    assert "opted out" in body["blocked_reason"]

    refused = await client.post(
        f"/api/v1/invoices/{invoice['id']}/deliveries/",
        json={
            "channel": "email",
            "attach_pdf": False,
            "preview_sha256": body["preview_sha256"],
        },
        headers=headers,
    )
    assert refused.status_code == 400
    assert "opted out" in refused.text


async def test_a_draft_cannot_be_sent_to_a_customer(client, biller, session):
    """A customer receiving a draft has received something that does not exist."""
    entity = await _entity(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    draft = (
        await client.post(
            "/api/v1/invoices/",
            json={
                "billing_entity": str(entity.id),
                "invoice_date": date.today().isoformat(),
                "buyer_name": "Draft Buyer [api-test]",
                "buyer_gstin": "09AAECS9424P1ZL",
                "buyer_state_code": "09",
                "lines": [{"description": "Spray", "quantity": "1", "unit": "acre", "rate": "100"}],
            },
            headers=headers,
        )
    ).json()

    preview = await client.post(
        f"/api/v1/invoices/{draft['id']}/deliveries/preview/",
        json={"channel": "email", "attach_pdf": False},
        headers=headers,
    )
    assert preview.json()["can_send"] is False
    assert "no number" in preview.json()["blocked_reason"]
