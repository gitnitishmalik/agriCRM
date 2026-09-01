"""
Bulk import — Phase 1, sprint 4.

🔴 The tests that matter most here are the ones that assert a *refusal*: an
import that commits without a confirmed lawful basis is R5 failing silently,
and R5 failing silently is indistinguishable from R5 working right up until
somebody asks for the paperwork.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.domain import imports
from backend.domain import normalise as norm

pytestmark = pytest.mark.asyncio


def _message(response) -> str:
    return response.json()["error"]["message"]


async def _source(session, *, kind: str, approved: bool = True) -> int:
    row = await session.execute(
        text(
            """
            INSERT INTO dq.source (code, name, kind, legal_basis, contains_pii, is_approved)
            VALUES (:code, :name, CAST(:kind AS dq.source_kind), :basis, true, :approved)
            RETURNING id
            """
        ),
        {
            "code": f"test_{kind}_{uuid.uuid4().hex[:8]}",
            "name": f"Test source ({kind})",
            "kind": kind,
            "basis": "Test fixture. Not a real legal basis.",
            "approved": approved,
        },
    )
    return row.scalar_one()


@pytest_asyncio.fixture
async def partner_source(session) -> int:
    return await _source(session, kind="partner_agreement")


@pytest_asyncio.fixture
async def registry_source(session) -> int:
    return await _source(session, kind="public_registry")


GOOD_BASIS = "MoU with Bhainswal Kisan FPC dated 12 May 2026, clause 7 covers member data sharing."
GOOD_REF = "MOU-2026-0412"


async def _land(client, headers, *, source_id, rows, entity_type="person"):
    response = await client.post(
        "/api/v1/imports/",
        headers=headers,
        json={
            "file_name": "members.xlsx",
            "entity_type": entity_type,
            "source_id": source_id,
            "mapping": {"Name": "first_name"},
            "rows": rows,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ---------------------------------------------------------------------------
# 🔴 R5 — the gate
# ---------------------------------------------------------------------------


async def test_commit_refused_without_legal_basis(
    client, data_ops, mfa_headers, partner_source, session
):
    """
    🔴 R5. The whole sprint exists for this assertion.

    A client that never calls `/legal-basis/` cannot commit, however complete
    the batch otherwise is.
    """
    headers = await mfa_headers(data_ops)
    batch_id = await _land(
        client, headers, source_id=partner_source, rows=[{"first_name": "Sunita"}]
    )

    response = await client.post(f"/api/v1/imports/{batch_id}/commit/", headers=headers)
    assert response.status_code == 409
    assert "R5" in _message(response)
    assert "lawful basis" in _message(response).lower()

    written = await session.scalar(text("SELECT count(*) FROM core.person"))
    assert written == 0, "a refused commit must write nothing"


async def test_commit_succeeds_once_basis_confirmed(
    client, data_ops, mfa_headers, partner_source, session
):
    headers = await mfa_headers(data_ops)
    batch_id = await _land(
        client,
        headers,
        source_id=partner_source,
        rows=[{"first_name": "Sunita", "last_name": "Devi"}],
    )

    confirmed = await client.post(
        f"/api/v1/imports/{batch_id}/legal-basis/",
        headers=headers,
        json={"basis": GOOD_BASIS, "consent_evidence_ref": GOOD_REF},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["legal_basis_confirmed"] is True
    assert confirmed.json()["consent_evidence_ref"] == GOOD_REF

    committed = await client.post(f"/api/v1/imports/{batch_id}/commit/", headers=headers)
    assert committed.status_code == 200, committed.text
    assert committed.json()["rows_created"] == 1
    assert committed.json()["reversible_until"] is not None

    name = await session.scalar(text("SELECT full_name FROM core.person"))
    assert name == "Sunita Devi"


async def test_legal_basis_rejects_a_token_answer(client, data_ops, mfa_headers, partner_source):
    """
    A single character in each box is the shape this control fails in.

    R5 says a named user confirms the basis. A confirmation that accepts "x"
    records a name against nothing, which is worse than no field at all —
    it produces paperwork that looks complete.
    """
    headers = await mfa_headers(data_ops)
    batch_id = await _land(
        client, headers, source_id=partner_source, rows=[{"first_name": "Sunita"}]
    )

    for basis, ref in (("x", GOOD_REF), (GOOD_BASIS, "")):
        response = await client.post(
            f"/api/v1/imports/{batch_id}/legal-basis/",
            headers=headers,
            json={"basis": basis, "consent_evidence_ref": ref},
        )
        assert response.status_code == 400


async def test_confirming_after_commit_is_refused(client, data_ops, mfa_headers, partner_source):
    """
    🔴 Backdating an approval is worse than not having one.

    A basis confirmed after the write records an approval that did not precede
    it, which is precisely the claim the audit trail exists to make.
    """
    headers = await mfa_headers(data_ops)
    batch_id = await _land(
        client, headers, source_id=partner_source, rows=[{"first_name": "Sunita"}]
    )
    await client.post(
        f"/api/v1/imports/{batch_id}/legal-basis/",
        headers=headers,
        json={"basis": GOOD_BASIS, "consent_evidence_ref": GOOD_REF},
    )
    await client.post(f"/api/v1/imports/{batch_id}/commit/", headers=headers)

    again = await client.post(
        f"/api/v1/imports/{batch_id}/legal-basis/",
        headers=headers,
        json={"basis": GOOD_BASIS, "consent_evidence_ref": GOOD_REF},
    )
    assert again.status_code == 409


async def test_commit_is_not_repeatable(client, data_ops, mfa_headers, partner_source, session):
    """A retried request must not write the rows twice."""
    headers = await mfa_headers(data_ops)
    batch_id = await _land(
        client, headers, source_id=partner_source, rows=[{"first_name": "Sunita"}]
    )
    await client.post(
        f"/api/v1/imports/{batch_id}/legal-basis/",
        headers=headers,
        json={"basis": GOOD_BASIS, "consent_evidence_ref": GOOD_REF},
    )
    first = await client.post(f"/api/v1/imports/{batch_id}/commit/", headers=headers)
    assert first.status_code == 200

    second = await client.post(f"/api/v1/imports/{batch_id}/commit/", headers=headers)
    assert second.status_code == 409
    assert "committed" in _message(second).lower()

    count = await session.scalar(text("SELECT count(*) FROM core.person"))
    assert count == 1


async def test_legal_basis_confirmation_is_audited(
    client, data_ops, mfa_headers, partner_source, session
):
    """🔴 Who confirmed a lawful basis is the first question after an incident."""
    headers = await mfa_headers(data_ops)
    batch_id = await _land(
        client, headers, source_id=partner_source, rows=[{"first_name": "Sunita"}]
    )
    await client.post(
        f"/api/v1/imports/{batch_id}/legal-basis/",
        headers=headers,
        json={"basis": GOOD_BASIS, "consent_evidence_ref": GOOD_REF},
    )

    logged = await session.execute(
        text(
            # 🔴 CAST, not a bare string. asyncpg will not compare `uuid` to
            # `character varying` — the same strictness `models/types.py`
            # documents for enum columns.
            "SELECT reason, filter_json FROM audit.data_access_log "
            "WHERE actor_user_id = CAST(:actor AS uuid) "
            "AND action = 'confirm_legal_basis'"
        ).bindparams(actor=str(data_ops.public_id))
    )
    row = logged.mappings().first()
    assert row is not None
    assert row["reason"] == GOOD_BASIS


async def test_dry_run_writes_nothing_and_says_it_is_blocked(
    client, data_ops, mfa_headers, partner_source, session
):
    headers = await mfa_headers(data_ops)
    batch_id = await _land(
        client,
        headers,
        source_id=partner_source,
        rows=[{"first_name": "Sunita"}, {"first_name": "Anil"}],
    )

    response = await client.post(f"/api/v1/imports/{batch_id}/dry-run/", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["rows_created"] == 2
    assert body["may_commit"] is False
    assert "R5" in body["blocked_reason"]
    assert len(body["sample"]) == 2

    assert await session.scalar(text("SELECT count(*) FROM core.person")) == 0


# ---------------------------------------------------------------------------
# 🔴 R1 / R4 at the door
# ---------------------------------------------------------------------------


async def test_person_import_refuses_an_institutional_source(
    client, data_ops, mfa_headers, registry_source
):
    """
    🔴 R4. A scraped registry cannot be the source of a person import.

    The same gate `/api/v1/people/` applies to a single row, applied to four
    thousand at once — which is the direction the pressure actually comes from.
    """
    headers = await mfa_headers(data_ops)
    response = await client.post(
        "/api/v1/imports/",
        headers=headers,
        json={
            "file_name": "scraped.xlsx",
            "entity_type": "person",
            "source_id": registry_source,
            "mapping": {},
            "rows": [{"first_name": "Anil"}],
        },
    )
    assert response.status_code == 400
    assert "personal data" in _message(response).lower()


async def test_organisation_import_accepts_an_institutional_source(
    client, data_ops, mfa_headers, registry_source
):
    """An FPO name and CIN are institutional facts — R4 does not bite here."""
    headers = await mfa_headers(data_ops)
    response = await client.post(
        "/api/v1/imports/",
        headers=headers,
        json={
            "file_name": "sfac.xlsx",
            "entity_type": "organisation",
            "source_id": registry_source,
            "mapping": {},
            "rows": [{"name": "Bhainswal Kisan Producer Company Limited"}],
        },
    )
    assert response.status_code == 201


async def test_unapproved_source_cannot_supply_an_import(client, data_ops, mfa_headers, session):
    """🔴 R1, checked at land rather than at commit."""
    unapproved = await _source(session, kind="partner_agreement", approved=False)
    headers = await mfa_headers(data_ops)
    response = await client.post(
        "/api/v1/imports/",
        headers=headers,
        json={
            "file_name": "x.xlsx",
            "entity_type": "person",
            "source_id": unapproved,
            "mapping": {},
            "rows": [{"first_name": "A"}],
        },
    )
    assert response.status_code == 400
    assert "not approved" in _message(response).lower()


async def test_a_field_agent_cannot_run_an_import(client, agent, partner_source):
    headers_response = await client.post(
        "/api/v1/auth/login/",
        json={"email": agent.email, "password": "correct-horse-battery-staple"},
    )
    headers = {"Authorization": f"Bearer {headers_response.json()['access']}"}
    response = await client.post(
        "/api/v1/imports/",
        headers=headers,
        json={
            "file_name": "x.xlsx",
            "entity_type": "person",
            "source_id": partner_source,
            "mapping": {},
            "rows": [{"first_name": "A"}],
        },
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Errors are report lines, not aborts
# ---------------------------------------------------------------------------


async def test_a_bad_row_does_not_take_the_batch_down(
    client, data_ops, mfa_headers, partner_source
):
    """Doc 06 stage 3: a validation failure is a `dq.import_row_error`, not an abort."""
    headers = await mfa_headers(data_ops)
    batch_id = await _land(
        client,
        headers,
        source_id=partner_source,
        rows=[
            {"first_name": "Sunita", "mobile": "9876543210"},
            {"first_name": "Anil", "mobile": "12345"},
            {"first_name": ""},
            {"first_name": "Ram"},
        ],
    )

    response = await client.post(f"/api/v1/imports/{batch_id}/dry-run/", headers=headers)
    body = response.json()
    assert body["rows_total"] == 4
    assert body["rows_created"] == 2
    assert body["rows_error"] == 2

    errors = await client.get(f"/api/v1/imports/{batch_id}/errors/", headers=headers)
    assert errors.status_code == 200
    codes = {e["error_code"] for e in errors.json()}
    assert "mobile_invalid" in codes
    assert "missing_required" in codes
    # 🔴 The original row comes back with the error, so the source file can be
    # corrected without cross-referencing line numbers by hand.
    assert all("raw" in e for e in errors.json())


async def test_dry_run_sample_masks_contact_values(client, data_ops, mfa_headers, partner_source):
    """
    🔴 R9 reaches the preview too.

    A dry-run sample of a member list is a screen full of personal data, and
    it is the screen most likely to be pasted into a chat thread.
    """
    headers = await mfa_headers(data_ops)
    batch_id = await _land(
        client,
        headers,
        source_id=partner_source,
        rows=[{"first_name": "Sunita", "mobile": "9876543210"}],
    )
    response = await client.post(f"/api/v1/imports/{batch_id}/dry-run/", headers=headers)
    sample = response.json()["sample"][0]["values"]
    assert sample["mobile"] == "+91*****3210"
    assert "9876543210" not in response.text


# ---------------------------------------------------------------------------
# 🔴 The upsert margin — what stops an import erasing an agent's work
# ---------------------------------------------------------------------------


def test_a_registry_value_cannot_replace_a_field_verified_one():
    """
    🔴 CLAUDE.md: "Never let a bulk import overwrite a human-verified value."

    Field collection is 0.95; the best any bulk source manages is 0.90. The
    margin is 0.15, so nothing clears it. This test is the arithmetic stated
    out loud, because the rule is only as good as the numbers behind it.
    """
    decision = imports.merge_field(
        field_name="total_area_ha",
        existing="2.3",
        incoming="3.5",
        existing_confidence=imports.SOURCE_CONFIDENCE["field_collection"],
        incoming_confidence=imports.SOURCE_CONFIDENCE["public_registry"],
    )
    assert decision.action == "contradiction"
    assert decision.existing == "2.3"


def test_no_source_kind_can_overwrite_field_collection():
    """The stronger form: check every kind, not the one that came to mind."""
    verified = imports.SOURCE_CONFIDENCE["field_collection"]
    for kind, confidence in imports.SOURCE_CONFIDENCE.items():
        assert not (confidence > verified + imports.CONFIDENCE_MARGIN), (
            f"{kind} at {confidence} would overwrite a field-verified value"
        )


def test_an_empty_existing_value_is_filled():
    decision = imports.merge_field(
        field_name="gstin",
        existing=None,
        incoming="09ABCDE1234F1Z5",
        existing_confidence=Decimal("0.95"),
        incoming_confidence=Decimal("0.50"),
    )
    assert decision.action == "take_incoming"


def test_agreement_refreshes_rather_than_rewrites():
    """Two sources agreeing is evidence, not a reason to rewrite the row."""
    decision = imports.merge_field(
        field_name="name",
        existing="Bhainswal Kisan FPC",
        incoming="Bhainswal Kisan FPC",
        existing_confidence=Decimal("0.60"),
        incoming_confidence=Decimal("0.90"),
    )
    assert decision.action == "refresh"


# ---------------------------------------------------------------------------
# 🔴 The bigha problem
# ---------------------------------------------------------------------------


def test_bigha_without_a_state_is_refused():
    """
    🔴 CLAUDE.md: "A bigha varies by state — use a state-keyed table, reject
    rather than guess."
    """
    with pytest.raises(norm.NormaliseError) as raised:
        norm.area_to_hectares("3", "bigha")
    assert raised.value.code == "area_bigha_no_state"


def test_bigha_in_an_ambiguous_state_is_refused():
    """UP's bigha varies by district. A state-level number there is still a guess."""
    with pytest.raises(norm.NormaliseError) as raised:
        norm.area_to_hectares("3", "bigha", state="Uttar Pradesh")
    assert raised.value.code == "area_bigha_ambiguous"


def test_bigha_converts_where_the_value_is_settled():
    assert norm.area_to_hectares("10", "bigha", state="West Bengal") == Decimal("1.3380")
    assert norm.area_to_hectares("10", "bigha", state="Bihar") == Decimal("2.5290")


def test_the_bigha_spread_is_why_guessing_fails():
    """
    The numbers, so a future edit that "simplifies" the table has to face them.

    West Bengal to Uttarakhand is not a rounding difference; it is a factor of
    more than four on every derived farmer_class and every project sizing.
    """
    wb = norm.area_to_hectares("100", "bigha", state="West Bengal")
    bihar = norm.area_to_hectares("100", "bigha", state="Bihar")
    assert bihar / wb > Decimal("1.8")


def test_acres_convert_and_out_of_range_areas_are_refused():
    assert norm.area_to_hectares("2.5", "acre") == Decimal("1.0117")
    with pytest.raises(norm.NormaliseError) as raised:
        norm.area_to_hectares("100000", "acre")
    assert raised.value.code == "area_range"


def test_an_unknown_unit_is_refused_not_assumed_hectares():
    with pytest.raises(norm.NormaliseError) as raised:
        norm.area_to_hectares("5", "killa")
    assert raised.value.code == "area_unknown_unit"


# ---------------------------------------------------------------------------
# 🔴 Dates are day-first
# ---------------------------------------------------------------------------


def test_dates_are_day_first():
    """🔴 `03/04/2026` is 3 April. Never 3 March."""
    assert norm.parse_date("03/04/2026") == datetime(2026, 4, 3, tzinfo=UTC).date()
    assert norm.parse_date("31/12/2025").month == 12
    assert norm.parse_date("2026-04-03") == datetime(2026, 4, 3, tzinfo=UTC).date()


def test_an_american_date_is_refused_rather_than_reinterpreted():
    """
    `04/31/2026` has no 31st month. It fails rather than silently becoming
    31 April — which is the failure a lenient parser produces.
    """
    with pytest.raises(norm.NormaliseError):
        norm.parse_date("04/31/2026")


def test_two_digit_years_land_in_the_past():
    assert norm.parse_date("15/08/68").year == 1968


# ---------------------------------------------------------------------------
# Other normalisers
# ---------------------------------------------------------------------------


def test_money_handles_lakh_crore_and_rupee_signs():
    assert norm.parse_money("₹ 12,50,000") == Decimal("1250000.00")
    assert norm.parse_money("12.5 Lakh") == Decimal("1250000.00")
    assert norm.parse_money("1.25 Cr") == Decimal("12500000.00")


def test_names_collapse_whitespace_and_keep_acronyms():
    assert norm.normalise_name("  sunita   devi ") == "Sunita Devi"
    assert norm.normalise_name("bhainswal kisan FPC") == "Bhainswal Kisan FPC"


def test_devanagari_names_are_preserved_verbatim():
    """CLAUDE.md: Devanagari preserved verbatim into `name_local`."""
    assert norm.normalise_name("सुनीता देवी") == "सुनीता देवी"


def test_booleans_accept_hindi():
    assert norm.parse_bool("हाँ") is True
    assert norm.parse_bool("नहीं") is False
    with pytest.raises(norm.NormaliseError):
        norm.parse_bool("maybe")


def test_cin_is_validated_not_just_uppercased():
    assert norm.normalise_cin("u01100dl2015ptc123456") == "U01100DL2015PTC123456"
    with pytest.raises(norm.NormaliseError):
        norm.normalise_cin("NOTACIN")


def test_role_addresses_are_recognised():
    """`info@` belongs on the organisation, not on a person (Doc 06 stage 2)."""
    assert norm.is_role_email("info@kisanfpc.in") is True
    assert norm.is_role_email("sunita@kisanfpc.in") is False


def test_the_importer_shares_one_phone_rule_with_the_rest_of_the_system():
    """
    🔴 Not a second implementation.

    An importer normalising phones its own way produces numbers that never
    match `contact_point.value_normalised`, which reads as "we don't have that
    number" rather than as a bug.
    """
    from backend.domain import pii

    assert norm.normalise_contact("mobile", "98765 43210") == pii.normalise_phone("9876543210")
