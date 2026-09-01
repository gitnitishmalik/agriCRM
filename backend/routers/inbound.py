"""
`/api/v1/messaging-webhooks/whatsapp/` — inbound messages.

🔴 The second unauthenticated endpoint in the service, and it is authenticated
by an HMAC over the raw request bytes plus a sender bound to one billing entity
in `crm.messaging_identity`. An unknown number is recorded and answered with
silence: replying at all — even "you are not registered" — confirms to whoever
is probing that the endpoint is live and that some numbers are.

GET is Meta's subscription handshake. It compares a configured verify token and
echoes a challenge; it reads nothing and writes nothing.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse

from backend.deps import SessionDep
from backend.domain import inbound as service

router = APIRouter(prefix="/api/v1/messaging-webhooks", tags=["messaging"])

#: A WhatsApp envelope is small. Anything much bigger is not one, and
#: buffering it to find out is the cheap half of a denial-of-service.
MAX_BODY_BYTES = 512 * 1024


@router.get("/whatsapp/", name="whatsapp_verify")
async def verify_subscription(
    mode: str = Query(default="", alias="hub.mode"),
    token: str = Query(default="", alias="hub.verify_token"),
    challenge: str = Query(default="", alias="hub.challenge"),
) -> PlainTextResponse:
    """
    Meta's subscription handshake. Echoes the challenge if the token matches.

    Constant-time comparison, and a refusal when no token is configured — an
    endpoint that accepts an empty token accepts anyone's subscription.
    """
    from backend.config import settings

    expected = settings.whatsapp_verify_token
    if not expected or mode != "subscribe" or not hmac.compare_digest(expected, token):
        return PlainTextResponse("forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return PlainTextResponse(challenge)


@router.post("/whatsapp/", name="whatsapp_inbound")
async def receive(request: Request, session: SessionDep) -> JSONResponse:
    """
    Take an envelope of messages.

    🔴 200 for anything durably recorded, including messages that failed
    signature verification or came from an unknown sender. Meta retries on
    non-2xx, and retrying a message we have deliberately ignored adds load and
    changes nothing.
    """
    body = await request.body()

    if len(body) > MAX_BODY_BYTES:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"status": "rejected", "reason": "payload too large"},
        )

    results = await service.ingest(
        session,
        provider="meta",
        channel="whatsapp",
        body=body,
        headers=dict(request.headers),
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "received",
            "messages": len(results),
            # Replies are sent back over the channel by the delivery outbox,
            # not returned here — this body reaches Meta's servers, not a user.
            "handled": sum(1 for item in results if item.proposal_id is not None),
        },
    )
