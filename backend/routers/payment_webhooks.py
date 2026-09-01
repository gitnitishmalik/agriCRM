"""
`POST /api/v1/payment-webhooks/{provider}/` — signed event ingestion.

🔴 **The only unauthenticated write endpoint in the service**, and every
property of it is there for that reason:

* It reads the **raw request bytes** and verifies a signature over them.
  Re-serialising a parsed body changes whitespace and key order, so a signature
  checked against the re-serialised form verifies a document nobody sent.
* It **stores the event before deciding** anything, so a rejected event leaves
  evidence. "We started receiving events we could not verify" is exactly the
  thing you want to be able to see afterwards.
* It **answers 200 for anything it has durably recorded**, including events it
  rejected. A gateway retries on non-2xx, and retrying an event we have
  deliberately quarantined adds load and changes nothing.
* It creates a payment **only** when the signature verifies, the event is
  fresh, and the amount, currency and reference all match an outstanding
  request. Anything else goes to the reconciliation queue.

It is declared in `deps.PRE_MFA` because a payment gateway has no user, no
token and no second factor. Its authentication is the HMAC.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from backend.deps import SessionDep
from backend.domain import payments as service

router = APIRouter(prefix="/api/v1/payment-webhooks", tags=["payments"])

#: A body larger than this is refused unread. A gateway event is a few
#: kilobytes; anything much bigger is not one, and buffering it to find out is
#: the cheap half of a denial-of-service.
MAX_BODY_BYTES = 256 * 1024


@router.post("/{provider}/", name="payment_webhook")
async def receive(provider: str, request: Request, session: SessionDep) -> JSONResponse:
    """
    Take one event. Quick, durable, and honest about what it did with it.
    """
    body = await request.body()

    if len(body) > MAX_BODY_BYTES:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"status": "rejected", "reason": "payload too large"},
        )

    event = await service.ingest_webhook(
        session,
        provider_name=provider,
        body=body,
        headers=dict(request.headers),
    )

    # 🔴 200 whatever the verdict, because the event is now durably recorded
    # and a retry would produce the same verdict. The body says what happened
    # so a provider's dashboard shows something meaningful.
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "received",
            "result": event.processing_result,
            "event_id": event.provider_event_id,
            "detail": event.mismatch_detail,
        },
    )
