"""
Delivery providers — email and WhatsApp, each with a deterministic fake.

🔴 **A provider reports one of three things: sent, retry, or failed.** That
three-way split is the whole contract, and it is what stops the outbox
retrying a bounce forever or giving up on a timeout. A provider that raised for
both would leave the outbox unable to tell "the network blipped" from "that
address does not exist", and it would guess — badly, and to a customer.

🔴 **The fakes record rather than send.** They exist so the outbox semantics,
the retry policy, the frozen-preview confirmation and the delivery history are
exercised in CI with no credentials and no possibility of a test emailing a
real customer.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("backend.messaging")


@dataclass(frozen=True)
class SendResult:
    """
    What happened.

    `retryable` is the field that matters: True means the outbox schedules
    another attempt with backoff, False means it stops. Getting that wrong in
    either direction is a customer problem — repeated sends, or a document
    that never arrives.
    """

    ok: bool
    provider_message_id: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    retryable: bool = False


class DeliveryProvider(Protocol):
    name: str
    channel: str

    async def send(
        self,
        *,
        recipient: str,
        subject: str | None,
        body: str,
        attachment: tuple[str, bytes, str] | None,
        idempotency_key: str,
    ) -> SendResult: ...


# ---------------------------------------------------------------------------
# Recipient validation
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def valid_email(value: str) -> bool:
    return bool(_EMAIL.match((value or "").strip()))


def normalise_phone(value: str) -> str | None:
    """
    🔴 E.164, per the project-wide convention. Returns None rather than a
    guess: a ten-digit number with no country code is unambiguous in India and
    a nine-digit one is a typo, and sending to a typo is sending to somebody.
    """
    digits = re.sub(r"[^\d+]", "", value or "")
    if digits.startswith("+91") and len(digits) == 13:
        return digits
    if digits.startswith("91") and len(digits) == 12:
        return f"+{digits}"
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    return None


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


@dataclass
class FakeEmailProvider:
    """Records what would have been sent. The default, and what CI uses."""

    name: str = "fake_email"
    channel: str = "email"
    #: Inspectable by tests. Deliberately not persisted — a real provider does
    #: not keep your outbox for you, and a fake that did would hide a bug.
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def send(
        self,
        *,
        recipient: str,
        subject: str | None,
        body: str,
        attachment: tuple[str, bytes, str] | None,
        idempotency_key: str,
    ) -> SendResult:
        if not valid_email(recipient):
            return SendResult(
                ok=False,
                error_code="invalid_recipient",
                error_detail=f"'{recipient}' is not an email address.",
                retryable=False,
            )

        self.sent.append(
            {
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "attachment": attachment[0] if attachment else None,
                "idempotency_key": idempotency_key,
            }
        )
        # Derived from the key, so a replay through the fake returns the same
        # id a real provider would return for a deduplicated send.
        message_id = "fake-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
        return SendResult(ok=True, provider_message_id=message_id)


class SmtpEmailProvider:
    """
    Real email over SMTP. Disabled until a host is configured.

    Distinguishes a transient SMTP failure (4xx, connection dropped) from a
    permanent one (5xx, no such mailbox), because the outbox's retry decision
    depends on it.
    """

    name = "smtp"
    channel = "email"

    def __init__(self) -> None:
        from backend.config import settings

        if not settings.smtp_host:
            raise RuntimeError(
                "EMAIL_PROVIDER=smtp but SMTP_HOST is empty. Configure the host, "
                "or leave EMAIL_PROVIDER=fake — the fake records deliveries "
                "without sending, which is what development and CI want."
            )
        self._settings = settings

    async def send(
        self,
        *,
        recipient: str,
        subject: str | None,
        body: str,
        attachment: tuple[str, bytes, str] | None,
        idempotency_key: str,
    ) -> SendResult:
        import asyncio
        import smtplib
        from email.message import EmailMessage

        if not valid_email(recipient):
            return SendResult(
                ok=False,
                error_code="invalid_recipient",
                error_detail=f"'{recipient}' is not an email address.",
                retryable=False,
            )

        settings = self._settings
        message = EmailMessage()
        message["From"] = settings.email_from
        message["To"] = recipient
        message["Subject"] = subject or "Invoice"
        # A stable Message-ID from the idempotency key, so a provider that
        # deduplicates on it does the right thing for us for free.
        message["Message-ID"] = (
            f"<{hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]}@agricrm>"
        )
        message.set_content(body)

        if attachment:
            filename, content, content_type = attachment
            maintype, _, subtype = content_type.partition("/")
            message.add_attachment(
                content, maintype=maintype, subtype=subtype or "octet-stream", filename=filename
            )

        def _send() -> SendResult:
            try:
                with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
                    smtp.starttls()
                    if settings.smtp_user:
                        smtp.login(settings.smtp_user, settings.smtp_password)
                    smtp.send_message(message)
            except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as error:
                # 🔴 Permanent. Retrying a refused recipient just repeats the
                # refusal, more slowly, forever.
                return SendResult(
                    ok=False,
                    error_code="recipient_refused",
                    error_detail=str(error),
                    retryable=False,
                )
            except (smtplib.SMTPException, OSError) as error:
                return SendResult(
                    ok=False, error_code="smtp_error", error_detail=str(error), retryable=True
                )
            return SendResult(ok=True, provider_message_id=message["Message-ID"])

        return await asyncio.to_thread(_send)


# ---------------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------------


@dataclass
class FakeWhatsAppProvider:
    name: str = "fake_whatsapp"
    channel: str = "whatsapp"
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def send(
        self,
        *,
        recipient: str,
        subject: str | None,
        body: str,
        attachment: tuple[str, bytes, str] | None,
        idempotency_key: str,
    ) -> SendResult:
        normalised = normalise_phone(recipient)
        if normalised is None:
            return SendResult(
                ok=False,
                error_code="invalid_recipient",
                error_detail=(
                    f"'{recipient}' is not a phone number this can dial. Numbers are "
                    f"stored E.164 (+91XXXXXXXXXX)."
                ),
                retryable=False,
            )

        self.sent.append(
            {"recipient": normalised, "body": body, "idempotency_key": idempotency_key}
        )
        return SendResult(
            ok=True,
            provider_message_id="fakewa-"
            + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16],
        )


def verify_whatsapp_signature(body: bytes, header: str, app_secret: str) -> bool:
    """
    Meta's `X-Hub-Signature-256`, computed over the raw body.

    🔴 Raw bytes, never a re-serialised dict. Meta signs the exact octets it
    sent; JSON round-tripping changes whitespace and key order, and the
    signature then verifies a document nobody sent.

    Returns False when unconfigured. A webhook endpoint that accepts unsigned
    events because no secret is set is an open write endpoint on the internet.
    """
    if not app_secret or not header:
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    supplied = header.removeprefix("sha256=").strip()
    return hmac.compare_digest(expected, supplied)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

#: Module-level so the fakes keep their record across a request within one
#: process, which is what the delivery tests inspect.
_FAKE_EMAIL = FakeEmailProvider()
_FAKE_WHATSAPP = FakeWhatsAppProvider()


def get_provider(channel: str) -> DeliveryProvider:
    """🔴 An unknown provider name raises rather than falling back to a fake."""
    from backend.config import settings

    if channel == "email":
        name = (settings.email_provider or "fake").lower()
        if name in ("fake", "fake_email"):
            return _FAKE_EMAIL
        if name == "smtp":
            return SmtpEmailProvider()
        raise RuntimeError(f"EMAIL_PROVIDER='{name}' is not a provider this build knows.")

    if channel == "whatsapp":
        name = (settings.whatsapp_provider or "fake").lower()
        if name in ("fake", "fake_whatsapp"):
            return _FAKE_WHATSAPP
        raise RuntimeError(
            f"WHATSAPP_PROVIDER='{name}' is not available. The Meta Cloud API "
            f"adapter needs a verified WABA, an approved template and a phone "
            f"number id — see DEPLOYMENT.md. 🔴 Until then, sending invoice "
            f"documents over WhatsApp stays disabled rather than half-built."
        )

    raise RuntimeError(f"'{channel}' is not a delivery channel.")
