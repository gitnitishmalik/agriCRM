"""
People, roles and contact points — Phase 1, sprint 3.

🔴 Most of what is asserted here is a compliance control rather than a
feature. The R4 tests in particular exist because the failure they describe is
silent: a collector writing a director's mobile against a `public_registry`
source produces rows that look exactly like lawful ones, and nothing surfaces
until somebody asks where the data came from.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.domain import pii
from backend.models.business import ContactPoint, Organisation, Person, PersonOrgRole

pytestmark = pytest.mark.asyncio


def _message(response) -> str:
    """
    The app wraps every error as `{"error": {"code", "message", ...}}`.

    Read through one helper so a change to that envelope is one edit here
    rather than thirty assertions that each look slightly wrong.
    """
    return response.json()["error"]["message"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _source(session, *, kind: str, approved: bool = True, code: str | None = None) -> int:
    """A `dq.source` row of a given kind, returning its id."""
    code = code or f"test_{kind}_{uuid.uuid4().hex[:8]}"
    row = await session.execute(
        text(
            """
            INSERT INTO dq.source (code, name, kind, legal_basis, contains_pii, is_approved)
            VALUES (:code, :name, CAST(:kind AS dq.source_kind), :basis, true, :approved)
            RETURNING id
            """
        ),
        {
            "code": code,
            "name": f"Test source ({kind})",
            "kind": kind,
            "basis": "Test fixture. Not a real legal basis.",
            "approved": approved,
        },
    )
    return row.scalar_one()


@pytest_asyncio.fixture
async def partner_source(session) -> int:
    """🔴 A source personal data may lawfully arrive through (R4)."""
    return await _source(session, kind="partner_agreement")


@pytest_asyncio.fixture
async def registry_source(session) -> int:
    """
    🔴 A source that may carry institutional facts and not personal data.

    SFAC and MCA lists are this kind. A name and a DIN are published by
    statute; a mobile number is not.
    """
    return await _source(session, kind="public_registry")


@pytest_asyncio.fixture
async def state_id(session) -> int:
    row = await session.execute(
        text(
            """
            INSERT INTO ref.state (id, lgd_code, name)
            VALUES (:id, :lgd, :name)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """
        ),
        {"id": 99, "lgd": 9901, "name": "Test State"},
    )
    return row.scalar_one()


@pytest_asyncio.fixture
async def organisation(session, state_id) -> Organisation:
    now = datetime.now(UTC)
    org = Organisation(
        type="fpo",
        status="active",
        legal_form="producer_company",
        name=f"Test Kisan FPC {uuid.uuid4().hex[:6]}",
        state_id=state_id,
        created_at=now,
        updated_at=now,
    )
    session.add(org)
    await session.flush()
    return org


@pytest_asyncio.fixture
async def person(session, partner_source, state_id) -> Person:
    now = datetime.now(UTC)
    row = Person(
        first_name="Sunita",
        last_name="Devi",
        father_or_spouse="Ram Kumar",
        state_id=state_id,
        primary_source_id=partner_source,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# 🔴 R4 — what personal data may arrive through
# ---------------------------------------------------------------------------


async def test_person_rejects_institutional_source(
    client, session, data_ops, mfa_headers, registry_source
):
    """
    🔴 A `public_registry` source cannot create a person.

    This is the SFAC CEO block, expressed as a test. The PDFs carry a name, a
    mobile and a personal email; the register row says "organisational data
    only", and this is what makes that sentence true of the write path rather
    than only of the collector that currently happens to skip it.
    """
    headers = await mfa_headers(data_ops)
    response = await client.post(
        "/api/v1/people/",
        headers=headers,
        json={"first_name": "Anil", "last_name": "Sharma", "source_id": registry_source},
    )
    assert response.status_code == 400
    assert "personal data" in _message(response).lower()
    assert "public_registry" in _message(response)


async def test_person_accepts_partner_source(client, data_ops, mfa_headers, partner_source):
    """A partner agreement is one of R4's four routes."""
    headers = await mfa_headers(data_ops)
    response = await client.post(
        "/api/v1/people/",
        headers=headers,
        json={
            "first_name": "Sunita",
            "last_name": "Devi",
            "father_or_spouse": "Ram Kumar",
            "source_id": partner_source,
        },
    )
    assert response.status_code == 201
    body = response.json()
    # 🔴 `full_name` is generated by the database, not assembled in Python.
    assert body["full_name"] == "Sunita Devi"
    assert body["primary_source_id"] == partner_source


async def test_person_rejects_unapproved_source(client, session, data_ops, mfa_headers):
    """🔴 R1 — a source without compliance sign-off supplies nothing."""
    unapproved = await _source(session, kind="partner_agreement", approved=False)
    headers = await mfa_headers(data_ops)
    response = await client.post(
        "/api/v1/people/",
        headers=headers,
        json={"first_name": "Test", "source_id": unapproved},
    )
    assert response.status_code == 400
    assert "not approved" in _message(response).lower()


async def test_contact_point_rejects_institutional_source(
    client, data_ops, mfa_headers, person, registry_source
):
    """
    🔴 The same gate on the contact point, not only on the person.

    Gating creation alone would leave the interesting half open: a person
    created lawfully, then a mobile attached to them from a scraped registry.
    """
    headers = await mfa_headers(data_ops)
    response = await client.post(
        f"/api/v1/people/{person.id}/contact-points",
        headers=headers,
        json={"kind": "mobile", "value": "9876543210", "source_id": registry_source},
    )
    assert response.status_code == 400
    assert "personal data" in _message(response).lower()


def test_pii_source_kinds_exclude_scraped_registries():
    """
    🔴 A structural assertion, deliberately duplicating the constant.

    `PII_SOURCE_KINDS` is a compliance boundary, and the four kinds named here
    are the ones a scraper writes through. If one is ever added, this test
    fails and the person adding it has to say so out loud in the diff — the
    same argument INVOICE.md makes for the copilot's action vocabulary.
    """
    for kind in (
        "public_registry",
        "official_website",
        "open_government_data",
        "industry_directory",
        "inferred",
        "unknown",
    ):
        assert kind not in pii.PII_SOURCE_KINDS


# ---------------------------------------------------------------------------
# 🔴 R9 — masking and the audit trail
# ---------------------------------------------------------------------------


async def test_contact_values_are_masked_by_default(
    client, session, biller, person, partner_source
):
    """A role without `contact.view_full` sees the last four digits and no more."""
    now = datetime.now(UTC)
    session.add(
        ContactPoint(
            person_id=person.id,
            kind="mobile",
            value_raw="9876543210",
            value_normalised="+919876543210",
            source_id=partner_source,
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()

    tokens = await client.post(
        "/api/v1/auth/login/",
        json={"email": biller.email, "password": "correct-horse-battery-staple"},
    )
    headers = {"Authorization": f"Bearer {tokens.json()['access']}"}

    response = await client.get(f"/api/v1/people/{person.id}/contact-points", headers=headers)
    assert response.status_code == 200
    point = response.json()[0]
    assert point["masked"] is True
    assert point["value"] == "+91*****3210"
    assert "9876543210" not in point["value"]


async def test_unmask_refused_without_capability(client, biller, person):
    """
    🔴 403, and the full value never reaches the payload.

    The check runs before the response is assembled — a shape that returned
    the raw value and let the client hide it would be a control any `curl`
    walks past.
    """
    tokens = await client.post(
        "/api/v1/auth/login/",
        json={"email": biller.email, "password": "correct-horse-battery-staple"},
    )
    headers = {"Authorization": f"Bearer {tokens.json()['access']}"}

    response = await client.get(
        f"/api/v1/people/{person.id}/contact-points?unmask=true", headers=headers
    )
    assert response.status_code == 403
    assert "contact.view_full" in _message(response)


async def test_unmask_writes_data_access_log(
    client, session, data_ops, mfa_headers, person, partner_source
):
    """🔴 R9's second half. An unmasked view nobody can audit is not a control."""
    now = datetime.now(UTC)
    session.add(
        ContactPoint(
            person_id=person.id,
            kind="mobile",
            value_raw="9876543210",
            value_normalised="+919876543210",
            source_id=partner_source,
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()

    headers = await mfa_headers(data_ops)
    response = await client.get(
        f"/api/v1/people/{person.id}/contact-points?unmask=true", headers=headers
    )
    assert response.status_code == 200
    assert response.json()[0]["value"] == "+919876543210"
    assert response.json()[0]["masked"] is False

    logged = await session.execute(
        text(
            """
            SELECT action, entity_type, record_count
            FROM audit.data_access_log
            WHERE actor_user_id = :actor AND action = 'view_pii'
            """
        ),
        {"actor": str(data_ops.public_id)},
    )
    rows = logged.all()
    assert rows, "an unmasked read must leave a row in audit.data_access_log"
    assert rows[0].entity_type == "core.contact_point"


async def test_organisation_contact_is_not_masked(
    client, session, biller, organisation, partner_source, person
):
    """
    An office switchboard is not personal data.

    Masking it would make the register useless for the job it exists for,
    which is the same argument `mask_gstin` makes in the admin console.
    """
    now = datetime.now(UTC)
    point = ContactPoint(
        organisation_id=organisation.id,
        kind="landline",
        value_raw="01123456789",
        value_normalised="+911123456789",
        source_id=partner_source,
        created_at=now,
        updated_at=now,
    )
    session.add(point)
    await session.flush()
    assert point.is_personal is False


# ---------------------------------------------------------------------------
# 🔴 R10 — bulk reads
# ---------------------------------------------------------------------------


def test_bulk_read_requires_a_reason():
    """Over the threshold, a typed reason is mandatory and a keystroke is not one."""
    from fastapi import HTTPException

    assert pii.check_bulk_reason(50, None) is None

    with pytest.raises(HTTPException) as raised:
        pii.check_bulk_reason(pii.BULK_PII_THRESHOLD, None)
    assert raised.value.status_code == 400

    with pytest.raises(HTTPException):
        pii.check_bulk_reason(pii.BULK_PII_THRESHOLD, "x")

    accepted = pii.check_bulk_reason(
        pii.BULK_PII_THRESHOLD, "Quarterly verification sweep for Bihar districts."
    )
    assert accepted.startswith("Quarterly")


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("091-98765-43210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("09876543210", "+919876543210"),
    ],
)
def test_phone_normalises_to_e164(raw, expected):
    assert pii.normalise_phone(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "98765432101234", "", "not a number"])
def test_bad_phone_is_rejected_not_guessed(raw):
    """
    🔴 Reject rather than guess.

    The same rule CLAUDE.md states for bigha-to-hectare conversion: a wrong
    value stored silently surfaces months later as an undeliverable message
    counted against the WhatsApp quality rating.
    """
    with pytest.raises(pii.NormalisationError):
        pii.normalise_phone(raw)


async def test_contact_point_stores_normalised_value(
    client, session, data_ops, mfa_headers, person, partner_source
):
    headers = await mfa_headers(data_ops)
    response = await client.post(
        f"/api/v1/people/{person.id}/contact-points",
        headers=headers,
        json={"kind": "mobile", "value": "98765 43210", "source_id": partner_source},
    )
    assert response.status_code == 201
    # 🔴 Echoed masked even to the caller who supplied it, so a client never
    # has one code path expecting a raw value.
    assert response.json()["masked"] is True
    assert response.json()["is_whatsapp_capable"] is True

    stored = await session.scalar(
        text("SELECT value_normalised FROM core.contact_point WHERE person_id = :pid").bindparams(
            pid=person.id
        )
    )
    assert stored == "+919876543210"


# ---------------------------------------------------------------------------
# Roles — closed, never deleted
# ---------------------------------------------------------------------------


async def test_role_is_closed_not_deleted(
    client, session, data_ops, mfa_headers, person, organisation
):
    """
    🔴 A post ends by being dated, so the register can still answer who held
    it. CLAUDE.md: close old role rows, never overwrite.
    """
    headers = await mfa_headers(data_ops)
    created = await client.post(
        f"/api/v1/people/{person.id}/roles",
        headers=headers,
        json={
            "organisation_id": str(organisation.id),
            "role": "chairman",
            "valid_from": "2024-04-01",
            "is_decision_maker": True,
        },
    )
    assert created.status_code == 201
    role_id = created.json()["id"]
    assert created.json()["is_current"] is True

    closed = await client.patch(
        f"/api/v1/people/{person.id}/roles/{role_id}",
        headers=headers,
        json={"valid_to": "2025-03-31"},
    )
    assert closed.status_code == 200
    assert closed.json()["is_current"] is False
    assert closed.json()["valid_to"] == "2025-03-31"

    still_there = await session.get(PersonOrgRole, uuid.UUID(role_id))
    assert still_there is not None, "closing a role must not delete the row"


async def test_router_exposes_no_role_delete():
    """
    🔴 Structural: there is no delete path for a role.

    A test that reads the source rather than exercising an endpoint, for the
    same reason `test_admin.py` proves the console cannot issue — the
    guarantee is the *absence* of a code path, and absence is not something a
    request can demonstrate.
    """
    from backend.routers import people as module

    methods = {
        method for route in module.router.routes for method in getattr(route, "methods", set())
    }
    assert "DELETE" not in methods


async def test_second_open_primary_contact_is_refused(
    client, data_ops, mfa_headers, person, organisation, session, partner_source
):
    """The DDL's partial unique index, surfaced as a 409 rather than a 500."""
    headers = await mfa_headers(data_ops)
    first = await client.post(
        f"/api/v1/people/{person.id}/roles",
        headers=headers,
        json={
            "organisation_id": str(organisation.id),
            "role": "chief_executive",
            "is_primary_contact": True,
        },
    )
    assert first.status_code == 201

    other = Person(
        first_name="Anil",
        last_name="Sharma",
        primary_source_id=partner_source,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(other)
    await session.flush()

    second = await client.post(
        f"/api/v1/people/{other.id}/roles",
        headers=headers,
        json={
            "organisation_id": str(organisation.id),
            "role": "chairman",
            "is_primary_contact": True,
        },
    )
    assert second.status_code == 409
    assert "primary contact" in _message(second).lower()


# ---------------------------------------------------------------------------
# The register itself
# ---------------------------------------------------------------------------


async def test_list_carries_no_contact_values(
    client, session, data_ops, mfa_headers, person, partner_source
):
    """
    🔴 The list endpoint returns names and posts. Never numbers.

    A directory of names is a working tool. A directory of mobiles is the
    thing that gets exported once and lives on a laptop forever.
    """
    now = datetime.now(UTC)
    session.add(
        ContactPoint(
            person_id=person.id,
            kind="mobile",
            value_raw="9876543210",
            value_normalised="+919876543210",
            source_id=partner_source,
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()

    headers = await mfa_headers(data_ops)
    response = await client.get("/api/v1/people/?limit=50", headers=headers)
    assert response.status_code == 200
    assert "9876543210" not in response.text
    assert "contact_points" not in response.text


async def test_unknown_filter_is_a_400(client, data_ops, mfa_headers):
    """
    🔴 A typo'd filter that silently does nothing is how someone exports the
    whole register believing they exported one district.
    """
    headers = await mfa_headers(data_ops)
    response = await client.get("/api/v1/people/?distrct=9001", headers=headers)
    assert response.status_code == 400
    assert "distrct" in _message(response)


async def test_list_by_organisation_returns_the_board(
    client, data_ops, mfa_headers, person, organisation
):
    headers = await mfa_headers(data_ops)
    await client.post(
        f"/api/v1/people/{person.id}/roles",
        headers=headers,
        json={"organisation_id": str(organisation.id), "role": "managing_director"},
    )

    response = await client.get(
        f"/api/v1/people/by-organisation/{organisation.id}", headers=headers
    )
    assert response.status_code == 200
    roles = response.json()
    assert len(roles) == 1
    assert roles[0]["role"] == "managing_director"
    assert roles[0]["organisation_name"] == organisation.name


async def test_din_is_unique(client, session, data_ops, mfa_headers, partner_source):
    """
    A DIN identifies exactly one director at the MCA, so two rows holding one
    is a duplicate person rather than two people.
    """
    headers = await mfa_headers(data_ops)
    din = f"{uuid.uuid4().int % 100000000:08d}"

    first = await client.post(
        "/api/v1/people/",
        headers=headers,
        json={"first_name": "Ram", "last_name": "Kumar", "din": din, "source_id": partner_source},
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/people/",
        headers=headers,
        json={"first_name": "R.", "last_name": "Kumar", "din": din, "source_id": partner_source},
    )
    assert second.status_code == 409
    assert "merge" in _message(second).lower()


def test_masks_match_the_admin_console():
    """
    🔴 One mask, two call sites.

    Two masks that differ by one digit are one bug away from one of them
    being reversible, so the JSON API and the server-rendered console must
    produce the same string.
    """
    from backend.admin import rendering

    assert pii.mask_phone("+919876543210") == rendering.mask_phone("+919876543210")
    assert pii.mask_email("sunita@fpc.in") == rendering.mask_email("sunita@fpc.in")
