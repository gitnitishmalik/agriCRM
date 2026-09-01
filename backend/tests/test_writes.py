"""
The write paths: creating, issuing, cancelling, paying, and duplicate blocking.

Every test here runs inside a transaction that is rolled back, so the register
these assertions touch is left exactly as it was found. That matters more than
usual for this file — it writes to a live database holding real invoices and
746 real organisations, and a test that leaked a row would be a test that
corrupted the thing it was checking.

🔴 The assertions are about rules, not amounts: that a number is permanent,
that a cancellation needs a reason, that amounts are computed server-side and
that a duplicate is refused. Those are the properties the DDL and CLAUDE.md
insist on, and they are what a port can quietly lose.
"""

from __future__ import annotations

import uuid
from datetime import date
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


async def _entity_id(session) -> uuid.UUID | None:
    from backend.models.billing import BillingEntity

    entity = await session.scalar(
        select(BillingEntity).where(BillingEntity.valid_to.is_(None)).limit(1)
    )
    return entity.id if entity else None


def _draft(entity_id, **overrides) -> dict:
    payload = {
        "billing_entity": str(entity_id),
        "invoice_date": "2026-08-15",
        "buyer_name": "Test Buyer [api-test]",
        # 🔴 A checksum-valid GSTIN, because the pre-issue checks refuse a
        # taxable supply without one — that is the whole point of D1 in
        # INVOICE.md §3, and a fixture that dodged the check would leave the
        # issue path untested against the rule it now enforces.
        "buyer_gstin": "09AAECS9424P1ZL",
        "buyer_state_code": "09",
        "tax_treatment": "igst",
        "tax_rate_pct": "18.00",
        "lines": [
            {
                "description": "Satellite crop monitoring",
                "quantity": "10",
                "unit": "acre",
                "rate": "1000",
            }
        ],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Invoice creation and arithmetic
# ---------------------------------------------------------------------------


async def test_amounts_are_computed_server_side(client, agent, session):
    """
    🔴 The client sends a quantity and a rate. Everything else is ours.

    10 acres at ₹1000 is ₹10,000 taxable, ₹1,800 IGST, ₹11,800 total. A client
    that could post its own totals could post an invoice whose lines do not
    sum to its header — internally inconsistent in a way nobody notices until
    an accounts team rejects the document.
    """
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    response = await client.post(
        "/api/v1/invoices/", json=_draft(entity), headers=await _headers(client, agent)
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert Decimal(body["taxable_value"]) == Decimal("10000.00")
    assert Decimal(body["tax_amount"]) == Decimal("1800.00")
    assert Decimal(body["total_value"]) == Decimal("11800.00")
    assert body["amount_in_words"].lower().startswith("inr eleven thousand eight hundred")


async def test_an_area_line_carries_its_hectare_conversion(client, agent, session):
    """
    🔴 CLAUDE.md: all area in hectares. Acres are an input convenience
    converted at the edge; 10 acres is 4.0469 ha.
    """
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    body = (
        await client.post(
            "/api/v1/invoices/", json=_draft(entity), headers=await _headers(client, agent)
        )
    ).json()

    hectares = Decimal(body["lines"][0]["quantity_ha"])
    assert hectares == Decimal("4.0469")


async def test_a_draft_has_no_number(client, agent, session):
    """A number is allocated at issue. A draft is not yet a document."""
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    body = (
        await client.post(
            "/api/v1/invoices/", json=_draft(entity), headers=await _headers(client, agent)
        )
    ).json()

    assert body["invoice_no"] is None
    assert body["status"] == "draft"


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


async def test_issuing_allocates_a_number_and_freezes_the_document(client, biller, session):
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    invoice = (await client.post("/api/v1/invoices/", json=_draft(entity), headers=headers)).json()

    issued = await client.post(f"/api/v1/invoices/{invoice['id']}/issue/", headers=headers)
    assert issued.status_code == 200, issued.text

    body = issued.json()
    assert body["status"] == "issued"
    assert body["invoice_no"], "no number was allocated"
    # 2026-08-15 falls in FY 2026-27 — April to March.
    assert "2026-27" in body["invoice_no"]


async def test_an_issued_invoice_cannot_be_issued_again(client, biller, session):
    """The point of no return is a point, not a suggestion."""
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    invoice = (await client.post("/api/v1/invoices/", json=_draft(entity), headers=headers)).json()
    await client.post(f"/api/v1/invoices/{invoice['id']}/issue/", headers=headers)

    again = await client.post(f"/api/v1/invoices/{invoice['id']}/issue/", headers=headers)
    assert again.status_code == 400
    assert "draft" in again.text.lower()


async def test_an_invoice_with_no_lines_cannot_be_issued(client, biller, session):
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    invoice = (
        await client.post("/api/v1/invoices/", json=_draft(entity, lines=[]), headers=headers)
    ).json()

    response = await client.post(f"/api/v1/invoices/{invoice['id']}/issue/", headers=headers)
    assert response.status_code == 400
    assert "at least one line" in response.text


async def test_an_issued_invoice_cannot_be_edited(client, biller, session):
    """
    🔴 Once issued the document exists in someone else's accounts. Changing it
    there is not an edit — it is a different document wearing the same number.
    """
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    invoice = (await client.post("/api/v1/invoices/", json=_draft(entity), headers=headers)).json()
    await client.post(f"/api/v1/invoices/{invoice['id']}/issue/", headers=headers)

    response = await client.patch(
        f"/api/v1/invoices/{invoice['id']}", json={"buyer_name": "Someone Else"}, headers=headers
    )
    assert response.status_code == 400


async def test_a_cancellation_needs_a_reason(client, biller, session):
    """
    🔴 Required here and by a database CHECK, because this is the field that
    was left blank throughout the historical data.
    """
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    invoice = (await client.post("/api/v1/invoices/", json=_draft(entity), headers=headers)).json()
    await client.post(f"/api/v1/invoices/{invoice['id']}/issue/", headers=headers)

    blank = await client.post(
        f"/api/v1/invoices/{invoice['id']}/cancel/", json={"reason": "   "}, headers=headers
    )
    assert blank.status_code == 400
    assert "reason" in blank.text.lower()


async def test_a_cancelled_invoice_keeps_its_number(client, biller, session):
    """
    🔴 The number is burned, not returned to the series. Two documents claiming
    to be the same invoice is the failure this prevents.
    """
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    invoice = (await client.post("/api/v1/invoices/", json=_draft(entity), headers=headers)).json()
    issued = (await client.post(f"/api/v1/invoices/{invoice['id']}/issue/", headers=headers)).json()

    cancelled = await client.post(
        f"/api/v1/invoices/{invoice['id']}/cancel/",
        json={"reason": "raised against the wrong entity"},
        headers=headers,
    )
    assert cancelled.status_code == 200
    body = cancelled.json()

    assert body["status"] == "cancelled"
    assert body["invoice_no"] == issued["invoice_no"], "the number changed on cancellation"


async def test_a_draft_is_discarded_not_cancelled(client, biller, session):
    """It never became a document, so there is nothing to cancel."""
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    invoice = (await client.post("/api/v1/invoices/", json=_draft(entity), headers=headers)).json()

    response = await client.post(
        f"/api/v1/invoices/{invoice['id']}/cancel/", json={"reason": "mistake"}, headers=headers
    )
    assert response.status_code == 400
    assert "discarded" in response.text


async def test_an_issued_invoice_cannot_be_deleted(client, biller, session):
    """A document that exists in someone's accounts cannot be un-existed."""
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    invoice = (await client.post("/api/v1/invoices/", json=_draft(entity), headers=headers)).json()
    await client.post(f"/api/v1/invoices/{invoice['id']}/issue/", headers=headers)

    response = await client.delete(f"/api/v1/invoices/{invoice['id']}", headers=headers)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


async def test_a_payment_reduces_what_is_outstanding(client, biller, session):
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    invoice = (await client.post("/api/v1/invoices/", json=_draft(entity), headers=headers)).json()
    await client.post(f"/api/v1/invoices/{invoice['id']}/issue/", headers=headers)

    paid = await client.post(
        f"/api/v1/invoices/{invoice['id']}/payments/",
        json={"amount": "5000.00", "received_on": str(date(2026, 8, 20)), "mode": "neft"},
        headers=headers,
    )
    assert paid.status_code == 200, paid.text
    body = paid.json()

    assert Decimal(body["amount_received"]) == Decimal("5000.00")
    assert Decimal(body["amount_outstanding"]) == Decimal("6800.00")


async def test_a_payment_against_a_draft_is_refused(client, biller, session):
    """Nothing is owed on a document that was never issued."""
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    invoice = (await client.post("/api/v1/invoices/", json=_draft(entity), headers=headers)).json()

    response = await client.post(
        f"/api/v1/invoices/{invoice['id']}/payments/",
        json={"amount": "100.00", "received_on": "2026-08-20"},
        headers=headers,
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Duplicate blocking
# ---------------------------------------------------------------------------


async def test_a_likely_duplicate_is_refused_with_its_candidates(client, agent, session):
    """
    🔴 409 with the candidates attached, not a silent create.

    A register that quietly accepts the same FPO twice is a register nobody can
    count, and the second copy is found months later by an agent visiting the
    same people twice.
    """
    from backend.models.business import Organisation

    existing = await session.scalar(
        select(Organisation).where(
            Organisation.is_deleted.is_(False), Organisation.district_id.isnot(None)
        )
    )
    if existing is None:
        pytest.skip("no organisation with a district to collide with")

    response = await client.post(
        "/api/v1/organisations/",
        json={
            "type": "fpo",
            "name": existing.name,
            "district_id": existing.district_id,
            "state_id": existing.state_id,
        },
        headers=await _headers(client, agent),
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["candidates"], "409 with no candidates is not actionable"
    assert detail["candidates"][0]["score"] >= 0.6


async def test_force_creates_anyway(client, agent, session):
    """The override exists; it is recorded, not silent."""
    from backend.models.business import Organisation

    existing = await session.scalar(
        select(Organisation).where(
            Organisation.is_deleted.is_(False), Organisation.district_id.isnot(None)
        )
    )
    if existing is None:
        pytest.skip("no organisation with a district to collide with")

    response = await client.post(
        "/api/v1/organisations/?force=true",
        json={
            "type": "fpo",
            "name": existing.name,
            "district_id": existing.district_id,
            "state_id": existing.state_id,
        },
        headers=await _headers(client, agent),
    )
    assert response.status_code == 201, response.text


async def test_check_duplicates_agrees_with_the_create_path(client, agent, session):
    """
    One scorer. Two implementations of "is this a duplicate" would eventually
    disagree, and the one the user saw would not be the one that blocked them.
    """
    from backend.models.business import Organisation

    existing = await session.scalar(
        select(Organisation).where(
            Organisation.is_deleted.is_(False), Organisation.district_id.isnot(None)
        )
    )
    if existing is None:
        pytest.skip("no organisation with a district to collide with")

    headers = await _headers(client, agent)
    checked = (
        await client.post(
            "/api/v1/organisations/check-duplicates/",
            json={"name": existing.name, "district_id": existing.district_id},
            headers=headers,
        )
    ).json()
    blocked = await client.post(
        "/api/v1/organisations/",
        json={"type": "fpo", "name": existing.name, "district_id": existing.district_id},
        headers=headers,
    )

    assert checked, "check-duplicates found nothing the create path blocks on"
    assert blocked.status_code == 409
    assert {c["id"] for c in checked} == {c["id"] for c in blocked.json()["detail"]["candidates"]}


async def test_a_soft_deleted_organisation_stays_resolvable(client, agent, session):
    """
    🔴 Nothing is hard-deleted. A stored id must not turn into a 404 that looks
    like the id was wrong.
    """
    headers = await _headers(client, agent)
    created = (
        await client.post(
            "/api/v1/organisations/?force=true",
            json={"type": "fpo", "name": f"Delete Me FPC {uuid.uuid4().hex[:6]}"},
            headers=headers,
        )
    ).json()

    assert (
        await client.delete(f"/api/v1/organisations/{created['id']}", headers=headers)
    ).status_code == 204

    fetched = await client.get(f"/api/v1/organisations/{created['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["is_deleted"] is True


# ---------------------------------------------------------------------------
# 🔴 No authenticated route may answer with a redirect
# ---------------------------------------------------------------------------


async def test_no_api_route_redirects_on_a_trailing_slash(client, biller):
    """
    🔴 The bug this exists for, and why it is a whole-router rule.

    FastAPI answers a trailing-slash mismatch with a 307 to an *absolute* URL
    on the backend origin. Behind the dev proxy that is a cross-origin
    redirect, and browsers strip `Authorization` across origins — so the retry
    arrives unauthenticated. The client sees 401, refreshes its token,
    retries, and loops. It reads in the log like an expiring session and is
    actually a missing slash:

        GET /api/v1/invoices/{id}/  -> 307
        GET /api/v1/invoices/{id}   -> 401   (header dropped)
        POST /api/v1/auth/refresh/  -> 200   (client thinks the token died)
        ... and round again

    A 307 that keeps the header is still wrong: it doubles every request. So
    the rule is that neither form redirects, and this walks every route to
    hold it rather than trusting the five that were fixed by hand.
    """
    from backend.main import app

    headers = await _headers(client, biller)
    offenders: list[str] = []

    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith("/api/v1") or "GET" not in methods:
            continue
        # Only paths we can call without inventing a body or a real id.
        if "{" in path:
            continue

        for candidate in (path, path.rstrip("/") if path.endswith("/") else path + "/"):
            response = await client.get(candidate, headers=headers, follow_redirects=False)
            if response.status_code in (301, 307, 308):
                offenders.append(
                    f"{candidate} -> {response.status_code} {response.headers.get('location', '')}"
                )

    assert offenders == [], (
        "these routes redirect, which drops the Authorization header across "
        f"the dev proxy: {offenders}"
    )


async def test_an_invoice_is_reachable_with_and_without_a_trailing_slash(client, biller, session):
    """
    Both forms answer directly. The frontend sends the trailing slash; the
    tests and any curl-by-hand tend not to, and neither should pay a redirect.
    """
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    created = await client.post("/api/v1/invoices/", json=_draft(entity), headers=headers)
    assert created.status_code == 201, created.text
    invoice_id = created.json()["id"]

    for path in (f"/api/v1/invoices/{invoice_id}", f"/api/v1/invoices/{invoice_id}/"):
        response = await client.get(path, headers=headers, follow_redirects=False)
        assert response.status_code == 200, f"{path} -> {response.status_code}"
        assert response.json()["id"] == invoice_id


# ---------------------------------------------------------------------------
# 🔴 The live preview, and the two ways it was broken
# ---------------------------------------------------------------------------


async def test_every_template_renders(client, biller, session):
    """
    🔴 T1, T2 and T3, each actually rendered.

    All three carried Django template syntax the port never converted —
    `|default:"—"`, `{% extends "billing/..." %}`, `forloop.last` — so document
    rendering raised for every template while the tests only ever exercised the
    JSON paths. A template error surfaces at render time, not import time,
    which is why nothing caught it until an invoice was previewed.
    """
    from backend.models.billing import BillingEntity

    headers = await _headers(client, biller)
    entities = list(
        await session.scalars(select(BillingEntity).where(BillingEntity.valid_to.is_(None)))
    )
    if not entities:
        pytest.skip("no billing entity seeded")

    for template in ("T1", "T2", "T3"):
        entity = entities[0]
        response = await client.post(
            "/api/v1/invoices/preview/",
            json={
                "billing_entity": str(entity.id),
                "invoice_date": "2026-08-30",
                "buyer_name": "Syngenta India Private Limited",
                "buyer_gstin": "09AAECS9424P1ZL",
                "buyer_state_code": "09",
                "tax_treatment": "igst",
                "tax_rate_pct": "18.00",
                "template_code": template,
                "lines": [
                    {
                        "description": "Drone spraying services",
                        "hsn_sac": "998611",
                        "quantity": "215",
                        "unit": "acre",
                        "rate": "150",
                    }
                ],
            },
            headers=headers,
        )
        assert response.status_code == 200, f"{template}: {response.text[:400]}"
        body = response.json()

        # 🔴 JSON carrying the document, not a bare HTML body. The create
        # screen renders `html` into an iframe *and* shows the figures beside
        # it — a raw `text/html` response left `result.html` undefined, so the
        # pane stayed blank while the request answered 200.
        assert "215" in body["html"], f"{template} rendered without its quantity"
        assert "Syngenta" in body["html"]

        # 215 acres at 150 is 32,250 taxable, 5,805 IGST, 38,055 total —
        # computed by `money.py`, formatted the Indian way by the server.
        assert body["total_value"] == "38055.00"
        assert body["display"]["total"] == "38,055.00"
        # And the area, in hectares, because that is the analysable column.
        assert body["total_area_ha"].startswith("87.00")


async def test_preview_accepts_either_entity_identifier(client, biller, session):
    """
    🔴 The create screen binds its dropdown to `entity_code` ("TEPL"); the
    register carries the row's UUID. The schema required only the UUID, so the
    live preview posted `entity_code` and got a 400 on every keystroke — a
    working renderer that looked broken.
    """
    from backend.models.billing import BillingEntity

    headers = await _headers(client, biller)
    entity = await session.scalar(
        select(BillingEntity).where(BillingEntity.valid_to.is_(None)).limit(1)
    )
    if entity is None:
        pytest.skip("no billing entity seeded")

    base = {
        "invoice_date": "2026-08-30",
        "buyer_name": "Test Buyer [api-test]",
        "lines": [],
    }

    by_id = await client.post(
        "/api/v1/invoices/preview/",
        json={**base, "billing_entity": str(entity.id)},
        headers=headers,
    )
    by_code = await client.post(
        "/api/v1/invoices/preview/",
        json={**base, "entity_code": entity.code},
        headers=headers,
    )

    assert by_id.status_code == 200, by_id.text[:300]
    assert by_code.status_code == 200, by_code.text[:300]

    neither = await client.post("/api/v1/invoices/preview/", json=base, headers=headers)
    assert neither.status_code == 400
    assert "entity_code" in neither.text


async def test_a_half_typed_line_previews_rather_than_erroring(client, biller, session):
    """
    🔴 An unfilled box is not an invalid number.

    A controlled React input holds `""` before anyone types, and the preview
    fires on every keystroke — so a quantity entered before a rate must render
    the document as far as it goes. Nothing here is saved and no number is
    allocated, so leniency costs nothing.
    """
    from backend.models.billing import BillingEntity

    headers = await _headers(client, biller)
    entity = await session.scalar(
        select(BillingEntity).where(BillingEntity.valid_to.is_(None)).limit(1)
    )
    if entity is None:
        pytest.skip("no billing entity seeded")

    for line in (
        {"description": "", "quantity": "", "unit": "acre", "rate": ""},
        {"description": "Spraying", "quantity": "215", "unit": "acre", "rate": ""},
        {"description": "Spraying", "quantity": "", "unit": "acre", "rate": "150"},
    ):
        response = await client.post(
            "/api/v1/invoices/preview/",
            json={
                "billing_entity": str(entity.id),
                "invoice_date": "2026-08-30",
                "buyer_name": "Half Typed [api-test]",
                "lines": [line],
            },
            headers=headers,
        )
        assert response.status_code == 200, f"{line} -> {response.text[:300]}"


async def test_creating_an_invoice_still_requires_a_quantity_and_rate(client, biller, session):
    """
    🔴 The other half of the split. The preview is lenient; *creating* a
    document is not — a line with no quantity is not a line.
    """
    entity = await _entity_id(session)
    if entity is None:
        pytest.skip("no billing entity seeded")

    headers = await _headers(client, biller)
    payload = _draft(entity)
    payload["lines"] = [{"description": "Spraying", "unit": "acre"}]

    response = await client.post("/api/v1/invoices/", json=payload, headers=headers)
    assert response.status_code == 400, response.text[:300]


async def test_the_document_prints_the_whole_issuer_block(client, biller, session):
    """
    🔴 Every field the template references is mapped, and reaches the page.

    Jinja renders an undefined name as an empty string rather than raising, so
    a column missing from the ORM mapping produces an invoice that *looks*
    fine — right length, right totals — with no issuer address, no bank
    branch, no signatory and no declaration. It rendered, and it was not a tax
    invoice.

    Asserting the rendered output rather than the model is deliberate: the
    model having a field proves nothing about whether the document shows it.
    """

    from backend.models.billing import BillingEntity

    headers = await _headers(client, biller)
    entity = await session.scalar(
        select(BillingEntity).where(BillingEntity.code == "TEPL", BillingEntity.valid_to.is_(None))
    )
    if entity is None:
        pytest.skip("billing entities not seeded — run `make seed-entities`")

    response = await client.post(
        "/api/v1/invoices/preview/",
        json={
            "entity_code": "TEPL",
            "invoice_date": "2026-08-30",
            "buyer_name": "Syngenta India Private Limited",
            "lines": [],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text[:300]
    html = response.json()["html"]

    for field in (
        "address_lines",
        "bank_account_no",
        "bank_branch",
        "gstin",
        "legal_name",
        "signatory_title",
        "declaration",
        "jurisdiction_note",
    ):
        value = getattr(entity, field)
        if not value:
            continue
        needles = value if isinstance(value, list) else [value]
        for needle in needles:
            # The templates escape and wrap, so compare on a distinctive
            # fragment rather than the whole string.
            fragment = str(needle).split(",")[0].strip()[:24]
            assert fragment in html, (
                f"`{field}` is set on the entity ({fragment!r}) and does not "
                f"appear on the rendered document — the column is probably "
                f"missing from the ORM mapping."
            )


async def test_every_entity_field_the_templates_use_is_mapped():
    """
    Structural companion to the test above: no template may reference a column
    the model does not map. Cheap, and it fails at the point the mismatch is
    introduced rather than at the next render.
    """
    import pathlib
    import re

    from backend.models.billing import BillingEntity

    templates = pathlib.Path("api/templates")
    referenced: set[str] = set()
    for path in templates.glob("invoice_*.html"):
        referenced |= set(re.findall(r"entity\.([a-z_]+)", path.read_text(encoding="utf-8")))

    mapped = {column.name for column in BillingEntity.__table__.columns}
    missing = referenced - mapped

    assert missing == set(), (
        f"these templates print entity fields the model does not map: "
        f"{sorted(missing)}. Jinja renders them blank, so the document loses "
        f"them silently."
    )
