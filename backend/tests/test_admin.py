"""
The data-operations console.

🔴 CLAUDE.md values Django Admin at roughly three months of frontend work for a
data-curation system, and names it the reason not to retire the Django service.
These tests are what makes retiring it safe: every page renders, the collected
data is visible with its provenance, PII is masked, and nothing in the console
can issue an invoice.

A rendering test is worth more here than it looks. A Jinja template with a typo
in an attribute name raises at request time, not at import time, so a console
with no tests is a console that is broken on the page nobody opened this week —
which is always the page you open during an incident.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.admin.security import SESSION_COOKIE
from backend.security import issue_pair

pytestmark = pytest.mark.anyio

#: Every console page. Parametrised rather than listed in one test so a
#: failure names the page that broke.
PAGES = [
    "/admin/",
    "/admin/organisations/",
    "/admin/geography/",
    "/admin/sources/",
    "/admin/provenance/",
    "/admin/contradictions/",
    "/admin/contradictions/?resolved=true",
    "/admin/invoices/",
    "/admin/receivables/",
    "/admin/deliveries/",
    "/admin/reconciliation/",
    "/admin/proposals/",
    "/admin/extractions/",
    "/admin/tax-codes/",
]


def _session(client, user) -> None:
    """Put a fully signed-in session on the client."""
    tokens = issue_pair(
        user.id,
        role=user.role,
        mfa_required=user.requires_mfa,
        mfa_satisfied=True,
    )
    client.cookies.set(SESSION_COOKIE, tokens["access"])


@pytest.mark.parametrize("path", PAGES)
async def test_every_console_page_renders(client, data_ops, session, path):
    """
    A template typo raises at request time, not at import time. Without this,
    the broken page is the one nobody opened this week — which is always the
    one you open during an incident.
    """
    _session(client, data_ops)
    response = await client.get(path, follow_redirects=False)
    assert response.status_code == 200, response.text[:900]
    assert "<html" in response.text
    # The nav renders on every page; if it did not, the base template failed
    # and the page is a fragment rather than a document.
    assert "Data operations console" in response.text


# ---------------------------------------------------------------------------
# 🔴 The collected data is visible, with its provenance
# ---------------------------------------------------------------------------


@pytest.fixture
async def collected_org(session):
    """
    An organisation as a collector would leave it: a row, provenance for each
    field naming the source and confidence, and one contradiction.
    """
    from backend.models.business import Contradiction, FieldProvenance, Organisation, Source

    source = await session.scalar(select(Source).where(Source.code == "sfac_fpo_list"))
    if source is None:
        source = await session.scalar(select(Source).limit(1))
    if source is None:
        pytest.skip("no dq.source rows to attribute collected data to")

    org = Organisation(
        type="fpo",
        legal_form="producer_company",
        name=f"Console Test FPO {uuid.uuid4().hex[:6]} [api-test]",
        cin=f"U01100BR2019PTC{uuid.uuid4().int % 1000000:06d}",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(org)
    await session.flush()

    for field_name, value in (("name", org.name), ("cin", org.cin)):
        session.add(
            FieldProvenance(
                entity_type="organisation",
                entity_id=org.id,
                field_name=field_name,
                value_text=value,
                source_id=source.id,
                source_reference="https://sfacindia.com/List-of-FPO-Statewise.aspx",
                confidence=Decimal("0.60"),
                collected_at=datetime.now(UTC),
                is_current=True,
            )
        )
    session.add(
        Contradiction(
            entity_type="organisation",
            entity_id=org.id,
            field_name="address_line1",
            value_a="Verified by a field visit",
            value_b="Something a collector read",
            detected_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return org, source


async def test_the_scraped_data_is_visible_with_its_source(
    client, data_ops, session, collected_org
):
    """
    🔴 The requirement in one assertion: a collected organisation shows *which
    source produced it*, at what confidence, and when.

    A registry row on its own is not interesting. What matters is that it can
    be traced back to the thing that asserted it.
    """
    org, source = collected_org
    _session(client, data_ops)

    listing = await client.get("/admin/organisations/", follow_redirects=False)
    assert listing.status_code == 200
    assert org.name in listing.text
    assert source.code in listing.text, "the list does not show which source produced the row"

    detail = await client.get(f"/admin/organisations/{org.id}", follow_redirects=False)
    assert detail.status_code == 200
    body = detail.text

    assert "Field provenance" in body
    assert source.code in body
    assert "0.60" in body, "the confidence the collector wrote is not shown"
    assert "sfacindia.com" in body, "the source reference is not linked"
    assert "Contradictions" in body


async def test_organisations_can_be_filtered_by_source(client, data_ops, session, collected_org):
    """
    “Show me every organisation whose current values came from this source” is
    the question you ask when a parser turns out to have been wrong for a
    month. It is unanswerable without joining provenance, so it is a filter.
    """
    org, source = collected_org
    _session(client, data_ops)

    matching = await client.get(
        f"/admin/organisations/?source={source.code}", follow_redirects=False
    )
    assert matching.status_code == 200
    assert org.name in matching.text

    other = await client.get(
        "/admin/organisations/?source=definitely-not-a-source", follow_redirects=False
    )
    assert other.status_code == 200
    assert org.name not in other.text


async def test_the_provenance_page_shows_raw_collected_values(
    client, data_ops, session, collected_org
):
    """The rawest view there is: one row per field per entity, as written."""
    org, source = collected_org
    _session(client, data_ops)

    response = await client.get(f"/admin/provenance/?source={source.code}", follow_redirects=False)
    assert response.status_code == 200
    assert org.name in response.text
    assert "cin" in response.text


async def test_the_source_register_shows_approval_and_pii_state(client, data_ops, session):
    """
    🔴 R1 and R4 made visible. The register is not documentation about the
    rules — a collector reads these columns and refuses to start.
    """
    _session(client, data_ops)
    response = await client.get("/admin/sources/", follow_redirects=False)
    assert response.status_code == 200

    body = response.text
    assert "Legal basis" in body
    assert "approved" in body.lower()
    assert "PII" in body


async def test_a_contradiction_can_be_adjudicated_and_records_who(
    client, data_ops, session, collected_org
):
    """
    🔴 A contradiction that disappeared without a name against it is a fact
    somebody changed and nobody can account for.
    """
    from backend.admin.security import CSRF_COOKIE
    from backend.models.business import Contradiction

    org, _ = collected_org
    _session(client, data_ops)
    client.cookies.set(CSRF_COOKIE, "test-csrf-token")

    row = await session.scalar(
        select(Contradiction).where(
            Contradiction.entity_id == org.id, Contradiction.resolved_at.is_(None)
        )
    )
    assert row is not None

    response = await client.post(
        f"/admin/contradictions/{row.id}/resolve",
        data={
            "csrf_token": "test-csrf-token",
            "keep": "a",
            "note": "Field visit is more recent than the registry",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    await session.refresh(row)
    assert row.resolved_at is not None
    assert row.resolved_by == data_ops.public_id
    assert "kept stored value" in row.resolution
    assert "Field visit" in row.resolution


# ---------------------------------------------------------------------------
# 🔴 What the console must not do or show
# ---------------------------------------------------------------------------


async def test_contact_details_are_masked_for_a_role_without_the_capability(
    client, biller, session
):
    """
    🔴 R9. A console that renders every phone number in a list view has made
    `audit.data_access_log` meaningless — everyone has seen everything, always.

    `project_manager` may read the registry and is not in the override roles,
    so the contact block is masked for them.
    """
    from backend.models.business import Organisation

    org = Organisation(
        type="private_company",
        legal_form="private_limited",
        name=f"Masking Test Org {uuid.uuid4().hex[:6]} [api-test]",
        billing_email="accounts@example.invalid",
        billing_phone="+919999900001",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(org)
    await session.flush()

    _session(client, biller)
    response = await client.get(f"/admin/organisations/{org.id}", follow_redirects=False)
    assert response.status_code == 200

    body = response.text
    assert "accounts@example.invalid" not in body, "an email was rendered in full"
    assert "+919999900001" not in body, "a phone number was rendered in full"
    assert "ac•••@example.invalid" in body
    assert "+91*****0001" in body


async def test_the_console_cannot_issue_an_invoice():
    """
    🔴 Structural, like the copilot's equivalent.

    Issuing allocates a permanent number and freezes a statutory document. It
    lives on the API behind the pre-issue checks; a console button that skipped
    them would be exactly the one somebody reaches for under pressure. Checked
    by reading the source, because the guarantee is that the path does not
    exist rather than that it is hard to reach.
    """
    import inspect

    from backend.admin import billing_views, router

    for module in (router, billing_views):
        source = inspect.getsource(module)
        for forbidden in (
            "_allocate_number",
            "invoice_no =",
            'status = "issued"',
            "InvoicePayment(",
            "issue_invoice",
        ):
            assert forbidden not in source, (
                f"{module.__name__} contains {forbidden!r} — issuing, cancelling "
                f"and recording payments belong on the API, behind their "
                f"confirmation flows."
            )


async def test_the_console_does_not_list_villages():
    """
    🔴 `ref.village` reaches ~660k rows. A changelist that scans it is a page
    that times out and a database that stops answering anything else.
    """
    import inspect

    from backend.admin import router

    source = inspect.getsource(router)
    assert "Village" not in source, "the console queries ref.village"


async def test_an_unauthenticated_visitor_is_sent_to_the_sign_in_page(client):
    """A browser gets a redirect to a form, not a JSON 401."""
    client.cookies.clear()
    response = await client.get("/admin/organisations/", follow_redirects=False)
    assert response.status_code == 303
    assert "/admin/login" in response.headers["location"]
    # And it carries them back to where they were going.
    assert "next=" in response.headers["location"]


async def test_the_sign_in_page_does_not_say_which_half_was_wrong(client, data_ops):
    """
    Distinguishing a wrong email from a wrong password turns the form into a
    way to enumerate who has an account.
    """
    client.cookies.clear()
    response = await client.post(
        "/admin/login",
        data={"email": "nobody@example.invalid", "password": "wrong", "next": "/admin/"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "not recognised" in response.text
    assert "no such user" not in response.text.lower()
    assert "password" not in response.text.lower().split("credentials")[0][-200:]
