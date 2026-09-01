"""
Inbound WhatsApp — sender isolation and the draft-only boundary.

🔴 The exit gate for I-9 names two of these: an unregistered sender cannot read
or mutate any tenant data, and voice can create a draft proposal but cannot
allocate a number. Both are asserted here, along with the signature and replay
controls that make the endpoint safe to expose at all.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.anyio

APP_SECRET = "test-whatsapp-app-secret"
KNOWN_SENDER = "+919999900001"
UNKNOWN_SENDER = "+919999000011"


def _envelope(*, sender: str, text: str | None = None, audio_id: str | None = None) -> bytes:
    message = {"id": f"wamid.{uuid.uuid4().hex}", "from": sender.lstrip("+")}
    if audio_id:
        message["type"] = "audio"
        message["audio"] = {"id": audio_id}
    else:
        message["type"] = "text"
        message["text"] = {"body": text or ""}

    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "waba-1",
                    "changes": [{"field": "messages", "value": {"messages": [message]}}],
                }
            ],
        }
    ).encode()


def _signed(body: bytes, secret: str = APP_SECRET) -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}", "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _app_secret(monkeypatch):
    """
    🔴 Without a configured secret the endpoint accepts nothing, which is
    correct and untestable. These tests configure one so the *signed* path can
    be exercised; `test_an_unsigned_message_is_not_acted_on` covers the other.
    """
    from backend.config import settings

    monkeypatch.setattr(settings, "whatsapp_app_secret", APP_SECRET, raising=False)
    monkeypatch.setattr(settings, "whatsapp_verify_token", "test-verify-token", raising=False)


@pytest.fixture
async def bound_sender(session, biller):
    """A number authorised for one billing entity and one user."""
    from backend.models.billing import BillingEntity
    from backend.models.invoice_ops import MessagingIdentity

    entity = await session.scalar(
        select(BillingEntity).where(BillingEntity.valid_to.is_(None)).limit(1)
    )
    if entity is None:
        pytest.skip("no billing entity seeded")

    identity = MessagingIdentity(
        channel="whatsapp",
        sender_address=KNOWN_SENDER,
        billing_entity_id=entity.id,
        user_id=biller.public_id,
        is_active=True,
        authorised_by=biller.public_id,
        authorised_at=datetime.now(UTC),
    )
    session.add(identity)
    await session.flush()
    return identity


# ---------------------------------------------------------------------------
# The handshake
# ---------------------------------------------------------------------------


async def test_the_subscription_handshake_needs_the_right_token(client):
    good = await client.get(
        "/api/v1/messaging-webhooks/whatsapp/",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token",
            "hub.challenge": "1234567890",
        },
    )
    assert good.status_code == 200
    assert good.text == "1234567890"

    bad = await client.get(
        "/api/v1/messaging-webhooks/whatsapp/",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "1234567890",
        },
    )
    assert bad.status_code == 403


# ---------------------------------------------------------------------------
# 🔴 Sender isolation
# ---------------------------------------------------------------------------


async def test_an_unregistered_sender_reads_nothing_and_gets_no_reply(client, session):
    """
    🔴 The I-9 exit gate.

    An unknown number is recorded and ignored. It gets no reply at all —
    answering "you are not registered" would confirm to whoever is probing that
    the endpoint is live and that some numbers are.
    """
    from backend.models.copilot import AiProposal
    from backend.models.invoice_ops import InboundInvoiceMessage

    proposals_before = len(list(await session.scalars(select(AiProposal))))

    body = _envelope(sender=UNKNOWN_SENDER, text="Invoice Syngenta for 215 acres at 150")
    response = await client.post(
        "/api/v1/messaging-webhooks/whatsapp/", content=body, headers=_signed(body)
    )
    assert response.status_code == 200
    assert response.json()["handled"] == 0

    stored = await session.scalar(
        select(InboundInvoiceMessage)
        .where(InboundInvoiceMessage.sender_address == UNKNOWN_SENDER)
        .order_by(InboundInvoiceMessage.received_at.desc())
    )
    assert stored is not None, "the message was not recorded"
    assert stored.identity_id is None
    assert stored.proposal_id is None
    assert "not bound" in stored.detail

    proposals_after = len(list(await session.scalars(select(AiProposal))))
    assert proposals_after == proposals_before, "an unknown sender created a proposal"


async def test_an_unsigned_message_is_not_acted_on(client, session, bound_sender):
    """
    Recorded with its verdict, then ignored. Storing it is what makes the
    rejection reviewable — a handler that returns early keeps no record of what
    it refused.
    """
    from backend.models.invoice_ops import InboundInvoiceMessage

    body = _envelope(sender=KNOWN_SENDER, text="100 acres spraying at 150 per acre")
    response = await client.post(
        "/api/v1/messaging-webhooks/whatsapp/",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["handled"] == 0

    stored = await session.scalar(
        select(InboundInvoiceMessage).order_by(InboundInvoiceMessage.received_at.desc())
    )
    assert stored.signature_verified is False
    assert stored.proposal_id is None
    assert "signature" in stored.detail


async def test_a_forged_signature_is_refused(client, session, bound_sender):
    from backend.models.invoice_ops import InboundInvoiceMessage

    body = _envelope(sender=KNOWN_SENDER, text="100 acres spraying at 150 per acre")
    response = await client.post(
        "/api/v1/messaging-webhooks/whatsapp/",
        content=body,
        headers=_signed(body, secret="not-the-app-secret"),
    )
    assert response.status_code == 200
    assert response.json()["handled"] == 0

    stored = await session.scalar(
        select(InboundInvoiceMessage).order_by(InboundInvoiceMessage.received_at.desc())
    )
    assert stored.signature_verified is False


async def test_a_redelivered_message_is_handled_once(client, session, bound_sender):
    """Meta retries on any non-2xx. Idempotent by provider message id."""
    from backend.models.invoice_ops import InboundInvoiceMessage

    body = _envelope(sender=KNOWN_SENDER, text="215 acres spraying at 150 per acre")
    headers = _signed(body)

    await client.post("/api/v1/messaging-webhooks/whatsapp/", content=body, headers=headers)
    await client.post("/api/v1/messaging-webhooks/whatsapp/", content=body, headers=headers)

    message_id = json.loads(body)["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
    rows = list(
        await session.scalars(
            select(InboundInvoiceMessage).where(
                InboundInvoiceMessage.provider_message_id == message_id
            )
        )
    )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 🔴 What a message may produce
# ---------------------------------------------------------------------------


async def test_a_bound_sender_gets_a_draft_proposal_and_no_number(client, session, bound_sender):
    """
    🔴 The other half of the exit gate: a message creates a proposal, and a
    proposal cannot allocate a number.
    """
    from backend.models.copilot import AiProposal
    from backend.models.invoice_ops import InboundInvoiceMessage

    body = _envelope(sender=KNOWN_SENDER, text="215 acres of drone spraying at 150 per acre")
    response = await client.post(
        "/api/v1/messaging-webhooks/whatsapp/", content=body, headers=_signed(body)
    )
    assert response.status_code == 200
    assert response.json()["handled"] == 1

    stored = await session.scalar(
        select(InboundInvoiceMessage).order_by(InboundInvoiceMessage.received_at.desc())
    )
    assert stored.identity_id == bound_sender.id
    assert stored.proposal_id is not None

    proposal = await session.scalar(select(AiProposal).where(AiProposal.id == stored.proposal_id))
    assert proposal.status == "pending"
    assert proposal.action == "create_draft"
    assert proposal.invoice_id is None, "a message created an invoice without confirmation"
    assert proposal.billing_entity_id == bound_sender.billing_entity_id


async def test_a_message_asking_to_issue_is_refused_and_told_why(client, session, bound_sender):
    """
    The refusal is quoted back over the channel. A user who asked WhatsApp to
    issue an invoice should be told why it will not, not left wondering.
    """
    from backend.domain import inbound as service

    body = _envelope(sender=KNOWN_SENDER, text="Issue invoice TEPL/2026-27/09 now please")
    results = await service.ingest(
        session,
        provider="meta",
        channel="whatsapp",
        body=body,
        headers=_signed(body),
    )
    assert len(results) == 1
    assert results[0].proposal_id is None
    assert "cannot issue an invoice" in results[0].reply


async def test_a_voice_note_does_not_become_an_invented_draft(client, session, bound_sender):
    """
    🔴 No transcription provider is configured, and the handler says so rather
    than guessing. A fabricated transcript would become an invoice draft.
    """
    from backend.domain import inbound as service
    from backend.models.invoice_ops import InboundInvoiceMessage

    body = _envelope(sender=KNOWN_SENDER, audio_id="media-abc123")
    results = await service.ingest(
        session, provider="meta", channel="whatsapp", body=body, headers=_signed(body)
    )

    assert results[0].proposal_id is None
    assert "transcription" in results[0].reply

    stored = await session.scalar(
        select(InboundInvoiceMessage).order_by(InboundInvoiceMessage.received_at.desc())
    )
    assert stored.kind == "audio"
    # 🔴 No audio is retained: the column does not exist, and the transcript is
    # null because nothing transcribed it.
    assert stored.transcript is None
    assert not hasattr(stored, "audio")


async def test_the_reply_carries_no_total(client, session, bound_sender):
    """
    🔴 Money is computed when the draft is applied. A figure in a chat message
    is a figure nobody can trace to a document.
    """
    from backend.domain import inbound as service

    body = _envelope(sender=KNOWN_SENDER, text="215 acres of drone spraying at 150 per acre")
    results = await service.ingest(
        session, provider="meta", channel="whatsapp", body=body, headers=_signed(body)
    )

    reply = results[0].reply
    assert "Nothing has been issued" in reply
    assert "Totals are calculated there" in reply
    # 215 × 150 = 32,250 and with tax 38,055. Neither may appear.
    assert "32250" not in reply.replace(",", "")
    assert "38055" not in reply.replace(",", "")
