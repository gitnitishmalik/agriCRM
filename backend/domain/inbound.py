"""
Inbound WhatsApp — signature, sender identity, and transcript-to-proposal.

🔴 **Three controls, and the second is the one that matters most:**

1. **Signature over raw bytes.** Meta signs the exact octets it sent; a
   signature checked against a re-serialised body verifies a document nobody
   sent. The route reads `await request.body()` and passes those bytes here.

2. **The sender is bound to exactly one billing entity and one user.** An
   unknown number resolves to nothing and therefore reads nothing — no
   organisation search, no invoice lookup, no acknowledgement that a tenant
   exists. The binding *is* the authorisation, and it lives in the database
   (`crm.messaging_identity`) rather than a config file, because revoking it
   has to be an auditable act.

3. **The reply is a draft preview.** A message can produce a proposal. It can
   never produce an issued invoice, a payment or a send — the copilot's action
   vocabulary has no word for any of those.

🔴 **Voice notes become a transcript, not a stored recording.** Retaining audio
needs a consent basis this module does not have, and the transcript is what the
proposal was built from anyway (INVOICE.md §12.3 A).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain import proposals as proposal_service
from backend.domain.scoping import EntityScope
from backend.models.accounts import User
from backend.models.invoice_ops import InboundInvoiceMessage, MessagingIdentity
from backend.providers.messaging import normalise_phone, verify_whatsapp_signature

logger = logging.getLogger("backend.inbound")


@dataclass
class InboundResult:
    """
    What happened to one message.

    🔴 `reply` is None for an unrecognised sender. Answering an unknown number
    at all — even with "you are not registered" — tells whoever is probing that
    the endpoint is live and that some numbers *are* registered. Silence is the
    correct response.
    """

    stored: InboundInvoiceMessage | None
    reply: str | None
    proposal_id: uuid.UUID | None = None


def parse_whatsapp(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Pull messages out of a Meta Cloud API webhook envelope.

    The envelope nests four levels deep and carries status callbacks alongside
    messages; only messages are returned. A shape that does not match yields
    nothing rather than raising — a provider adding a field must not take the
    endpoint down.
    """
    messages: list[dict[str, Any]] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            messages.extend(value.get("messages", []) or [])
    return messages


async def resolve_identity(
    session: AsyncSession, *, channel: str, sender: str
) -> MessagingIdentity | None:
    """
    🔴 The authorisation. An unknown sender resolves to None and reads nothing.
    """
    normalised = normalise_phone(sender) or sender
    return await session.scalar(
        select(MessagingIdentity).where(
            MessagingIdentity.channel == channel,
            MessagingIdentity.sender_address == normalised,
            MessagingIdentity.is_active.is_(True),
            MessagingIdentity.revoked_at.is_(None),
        )
    )


async def transcribe(media_id: str) -> tuple[str | None, float | None]:
    """
    Voice note to text.

    Not implemented: speech-to-text needs a provider under an approved
    data-processing arrangement, and INVOICE.md §12.8 requires that to be
    documented before audio leaves the building. The seam exists so the rest
    of the path — transcript → proposal → preview — is built and tested; the
    provider is a configuration decision, not a code one.

    🔴 Returns None rather than a guess. A fabricated transcript would become
    an invoice draft.
    """
    logger.info("Voice transcription requested for media %s; no provider configured.", media_id)
    return None, None


async def handle_message(
    session: AsyncSession,
    *,
    provider: str,
    channel: str,
    message: dict[str, Any],
    signature_verified: bool,
) -> InboundResult:
    """
    Store one message, then decide what — if anything — to do with it.

    Stored first, exactly as the webhook path stores a payment event before
    trusting it: a message from an unknown sender is the interesting one, and a
    handler that returns early keeps no record of it.
    """
    provider_message_id = str(message.get("id") or f"missing-{uuid.uuid4().hex}")
    sender = str(message.get("from") or "")

    existing = await session.scalar(
        select(InboundInvoiceMessage).where(
            InboundInvoiceMessage.provider == provider,
            InboundInvoiceMessage.provider_message_id == provider_message_id,
        )
    )
    if existing is not None:
        # Meta redelivers on any non-2xx. Idempotent by provider message id.
        return InboundResult(stored=existing, reply=None)

    kind = str(message.get("type") or "text")
    body = None
    transcript = None
    confidence = None

    if kind == "text":
        body = (message.get("text") or {}).get("body")
    elif kind == "audio":
        media_id = str((message.get("audio") or {}).get("id") or "")
        transcript, confidence = await transcribe(media_id)

    identity = await resolve_identity(session, channel=channel, sender=sender)

    row = InboundInvoiceMessage(
        channel=channel,
        provider=provider,
        provider_message_id=provider_message_id,
        sender_address=normalise_phone(sender) or sender,
        identity_id=identity.id if identity else None,
        signature_verified=signature_verified,
        received_at=datetime.now(UTC),
        kind=kind,
        body=body,
        transcript=transcript,
        transcript_confidence=confidence,
        handled=False,
    )
    session.add(row)
    await session.flush()

    if not signature_verified:
        row.detail = "signature could not be verified; the message was not acted on"
        await session.flush()
        return InboundResult(stored=row, reply=None)

    if identity is None:
        # 🔴 Recorded and ignored. No reply, because replying confirms the
        # endpoint is live and that some numbers are registered.
        row.detail = "sender is not bound to a billing entity"
        await session.flush()
        logger.warning("Inbound %s from an unregistered sender was recorded and ignored.", channel)
        return InboundResult(stored=row, reply=None)

    text = (body or transcript or "").strip()
    if not text:
        row.detail = (
            "no usable text — a voice note needs a transcription provider, which is not configured"
            if kind == "audio"
            else "the message carried no text"
        )
        row.handled = True
        await session.flush()
        return InboundResult(
            stored=row,
            reply=(
                "I could not read that message. Voice notes need transcription, "
                "which is not switched on yet — please send the details as text."
            ),
        )

    user = await session.scalar(select(User).where(User.public_id == identity.user_id))
    if user is None or not user.is_active:
        row.detail = "the bound user is inactive"
        await session.flush()
        return InboundResult(stored=row, reply=None)

    from backend.deps import Caller

    caller = Caller(user, {"role": user.role, "mfa_required": False, "mfa_satisfied": True})
    scope = EntityScope(caller, [identity.billing_entity_id])

    try:
        proposal = await proposal_service.create_proposal(
            session,
            scope,
            request_text=text,
            action="create_draft",
            billing_entity_id=identity.billing_entity_id,
            source=channel,
        )
    except proposal_service.ProposalError as error:
        row.handled = True
        row.detail = str(error.detail)
        await session.flush()
        # 🔴 The refusal is quoted back. A user who asked WhatsApp to issue an
        # invoice should be told why it will not, not left wondering.
        return InboundResult(stored=row, reply=str(error.detail))

    row.proposal_id = proposal.id
    row.handled = True
    await session.flush()

    return InboundResult(stored=row, reply=_summarise(proposal), proposal_id=proposal.id)


def _summarise(proposal) -> str:
    """
    The reply. A description of a draft, and an instruction to go and look.

    🔴 It carries no total. Money is computed when the draft is applied, and a
    figure in a chat message would be a figure nobody can trace to a document.
    """
    patch = proposal.proposed_patch or {}
    lines = patch.get("lines") or []

    parts = ["Draft prepared. Nothing has been issued."]
    if patch.get("buyer_name"):
        parts.append(f"Customer: {patch['buyer_name']}")
    for index, line in enumerate(lines, 1):
        description = line.get("description") or "(service not named)"
        quantity = line.get("quantity")
        unit = line.get("unit")
        rate = line.get("rate")
        if quantity and rate:
            parts.append(f"Line {index}: {description} — {quantity} {unit or ''} at {rate}")
        else:
            parts.append(f"Line {index}: {description}")

    if proposal.missing_fields:
        parts.append("Still needed: " + ", ".join(proposal.missing_fields))

    parts.append(
        "Open the invoice screen to review the figures and issue it. "
        "Totals are calculated there, not here."
    )
    return "\n".join(parts)


async def ingest(
    session: AsyncSession,
    *,
    provider: str,
    channel: str,
    body: bytes,
    headers: dict[str, str],
) -> list[InboundResult]:
    """Verify the envelope's signature once, then handle each message in it."""
    import json

    from backend.config import settings

    lower = {key.lower(): value for key, value in headers.items()}
    verified = verify_whatsapp_signature(
        body, lower.get("x-hub-signature-256", ""), settings.whatsapp_app_secret
    )

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("Inbound %s webhook body was not JSON.", channel)
        return []

    results = []
    for message in parse_whatsapp(payload):
        results.append(
            await handle_message(
                session,
                provider=provider,
                channel=channel,
                message=message,
                signature_verified=verified,
            )
        )
    return results
