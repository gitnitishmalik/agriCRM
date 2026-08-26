"""
Phase 1 — organisation registry.

Two kinds of assertion live here and they are worth telling apart.

The first kind checks that the `managed = False` models actually agree with
`sql/schema.sql`. There is no migration keeping them honest — Django is not
allowed to alter these tables — so a column renamed in the DDL is a runtime
error that no type checker and no migration check will catch. Writing a row
through every model and reading it back is what closes that gap, and it is why
`conftest.py` applies the real DDL to the test database rather than letting
Django invent one.

The second kind checks behaviour the exit gate names directly: duplicate
blocking on create, soft delete that leaves the row resolvable, and the source
register refusing an approval it cannot justify.
"""

from __future__ import annotations

import uuid

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.dataquality.admin import SourceAdminForm
from apps.dataquality.models import QualityTier, Source, SourceKind
from apps.geography.models import Block, District, State, Village
from apps.organisations.admin import OrganisationAdminForm
from apps.organisations.dedupe import find_duplicates, name_similarity, normalise_name
from apps.organisations.models import (
    FpoProfile,
    Organisation,
    OrgStatus,
    OrgType,
    SugarMillProfile,
)

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"

# Uttar Pradesh. Seeded by sql/seed_reference.sql, so tests share the ids the
# application will actually see rather than inventing their own.
UP_STATE_ID = 9


@pytest.fixture
def data_ops() -> User:
    return User.objects.create_user(
        email="ops@thetaanalytics.in",
        password=PASSWORD,
        full_name="Data Ops",
        role=Role.DATA_OPS,
    )


@pytest.fixture
def client(data_ops) -> APIClient:
    api = APIClient()
    api.force_authenticate(user=data_ops)
    return api


@pytest.fixture
def district() -> District:
    return District.objects.create(
        lgd_code=146, state_id=UP_STATE_ID, name="Muzaffarnagar", name_local="मुज़फ़्फ़रनगर"
    )


@pytest.fixture
def other_district() -> District:
    return District.objects.create(lgd_code=135, state_id=UP_STATE_ID, name="Bijnor")


@pytest.fixture
def approved_source() -> Source:
    """
    The SFAC directory, as seeded by `sql/seed_reference.sql`.

    Fetched rather than created: the source register ships with the project
    and a test that invents its own row proves nothing about the one the
    collectors will actually read.
    """
    return Source.objects.get(code="sfac_fpo_list")


def make_org(**overrides) -> Organisation:
    defaults = {
        "name": "Bhainswal Kisan Producer Company Limited",
        "type": OrgType.FPO,
        "state_id": UP_STATE_ID,
    }
    return Organisation.objects.create(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# The models agree with the DDL
# ---------------------------------------------------------------------------


def test_geography_hierarchy_round_trips(district):
    """State to village, through the real ref tables."""
    block = Block.objects.create(lgd_code=1462, district=district, name="Budhana")
    village = Village.objects.create(
        lgd_code=146201,
        block=block,
        district=district,
        name="Bhainswal",
        pincode="251309",
        latitude="29.383000",
        longitude="77.512000",
    )

    fetched = Village.objects.select_related("block", "district", "district__state").get(
        pk=village.pk
    )
    assert fetched.block.name == "Budhana"
    assert fetched.district.state.name == "Uttar Pradesh"
    assert fetched.district.state.lgd_code == UP_STATE_ID


def test_seed_reference_states_are_present():
    """`seed_reference.sql` is reference data, not a fixture — assert it landed."""
    assert State.objects.count() >= 28
    assert State.objects.filter(name="Uttar Pradesh", lgd_code=9).exists()
    assert State.objects.filter(is_ut=True).exists()


def test_organisation_writes_every_column_group(district, approved_source, data_ops):
    """
    A round trip through the columns most likely to drift: arrays, jsonb, the
    numeric scales, and the user references that join on `public_id`.
    """
    org = Organisation.objects.create(
        org_code="FPO-UP-000123",
        name="Bhainswal Kisan Producer Company Limited",
        name_local="भैंसवाल किसान प्रोड्यूसर कंपनी लिमिटेड",
        short_name="Bhainswal KPCL",
        aliases=["Bhainswal FPC", "भैंसवाल एफपीसी"],
        type=OrgType.FPO,
        status=OrgStatus.ACTIVE,
        cin="U01100UP2019PTC123456",
        state_id=UP_STATE_ID,
        district=district,
        pincode="251309",
        member_count=1243,
        women_member_count=402,
        annual_turnover_inr="18450000.00",
        turnover_fy="2024-25",
        quality_tier=QualityTier.SILVER,
        completeness_score=62,
        primary_source=approved_source,
        owner_user=data_ops,
        tags=["cane", "priority"],
        extra={"cluster": "UP-West-3"},
    )

    fetched = Organisation.objects.select_related("owner_user", "primary_source").get(pk=org.pk)
    assert fetched.aliases == ["Bhainswal FPC", "भैंसवाल एफपीसी"]
    assert fetched.extra == {"cluster": "UP-West-3"}
    assert fetched.tags == ["cane", "priority"]
    assert str(fetched.annual_turnover_inr) == "18450000.00"
    assert fetched.primary_source.code == "sfac_fpo_list"
    # 🔴 The DDL types every user reference as uuid and carries no FK back to
    # accounts_user. This is the assertion that the public_id join works.
    assert fetched.owner_user == data_ops
    assert fetched.owner_user_id == data_ops.public_id


def test_profiles_extend_the_base_row(district):
    fpo = make_org(district=district)
    FpoProfile.objects.create(
        organisation=fpo,
        paid_up_capital="1250000.00",
        shareholder_count=1243,
        business_lines=["input sale", "output aggregation"],
        licences=["seed", "fertiliser"],
        primary_crops=[1, 2],
        has_storage=True,
        storage_capacity_mt="450.00",
    )

    mill = make_org(name="Rohana Kalan Sugar Mills", type=OrgType.SUGAR_MILL, district=district)
    SugarMillProfile.objects.create(
        organisation=mill,
        crushing_capacity_tcd=7500,
        avg_recovery_pct="11.25",
        has_ethanol_plant=True,
        distillery_capacity_klpd="120.00",
        season_start_month=11,
        season_end_month=4,
        federation_membership=["ISMA", "UP Sugar Mills Association"],
    )

    assert Organisation.objects.get(pk=fpo.pk).fpo_profile.business_lines == [
        "input sale",
        "output aggregation",
    ]
    reloaded_mill = Organisation.objects.get(pk=mill.pk)
    assert reloaded_mill.sugar_mill_profile.crushing_capacity_tcd == 7500
    assert not hasattr(reloaded_mill, "fpo_profile")


def test_db_check_constraints_are_live(district):
    """
    The DDL constraint the ORM cannot express, exercised.

    `org_women_le_members` is a table check. If this ever stops raising, the
    models have been pointed at tables Django created rather than the ones the
    DDL owns — which is the failure mode `managed = False` exists to avoid.
    """
    from django.db.utils import IntegrityError

    with pytest.raises(IntegrityError):
        make_org(district=district, member_count=100, women_member_count=200)


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------


def test_normalise_name_strips_legal_form():
    assert normalise_name("Kisan Unnati Farmer Producer Company Limited") == ["kisan", "unnati"]
    assert normalise_name("Kisan Unnati FPC Ltd.") == ["kisan", "unnati"]


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Suffix noise: the same body written two ways
        ("Kisan Unnati Farmer Producer Company Limited", "Kisan Unnati FPC Ltd."),
        # Token order
        ("Sahkari Ganna Vikas Samiti Bijnor", "Bijnor Sahkari Ganna Vikas Samiti"),
        # Transliteration drift
        ("Chaudhary Krishi Producer Company", "Choudhary Krushi Producer Company"),
    ],
)
def test_similar_names_score_above_the_block_threshold(left, right):
    assert name_similarity(left, right) >= 0.6


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Bijnor Kisan Producer Company", "Bijapur Kisan Producer Company"),
        ("Rohana Kalan Sugar Mills", "Khatauli Sugar Mills"),
    ],
)
def test_different_names_score_below_the_block_threshold(left, right):
    assert name_similarity(left, right) < 0.6


def test_duplicate_search_is_scoped_to_the_district(district, other_district):
    make_org(name="Kisan Seva Producer Company Limited", district=district)

    inside = find_duplicates("Kisan Seva FPC Ltd", district_id=district.pk)
    assert [c.organisation.name for c in inside] == ["Kisan Seva Producer Company Limited"]

    # The same name in the next district is a different organisation, and
    # blocking on it would teach analysts to tick the override reflexively.
    assert find_duplicates("Kisan Seva FPC Ltd", district_id=other_district.pk) == []


# ---------------------------------------------------------------------------
# Duplicate blocking — Phase 1 exit gate
# ---------------------------------------------------------------------------


def test_admin_form_blocks_a_duplicate(district):
    existing = make_org(name="Kisan Unnati Farmer Producer Company Limited", district=district)

    form = OrganisationAdminForm(
        data={
            "name": "Kisan Unnati FPC Ltd",
            "type": OrgType.FPO,
            "status": OrgStatus.PROSPECT,
            "legal_form": "producer_company",
            "quality_tier": QualityTier.BRONZE,
            "completeness_score": 0,
            "district": district.pk,
            "state": UP_STATE_ID,
        }
    )
    assert not form.is_valid()
    assert existing.name in str(form.errors)


def test_admin_form_saves_when_the_override_is_ticked(district):
    make_org(name="Kisan Unnati Farmer Producer Company Limited", district=district)

    form = OrganisationAdminForm(
        data={
            "name": "Kisan Unnati FPC Ltd",
            "type": OrgType.FPO,
            "status": OrgStatus.PROSPECT,
            "legal_form": "producer_company",
            "quality_tier": QualityTier.BRONZE,
            "completeness_score": 0,
            "district": district.pk,
            "state": UP_STATE_ID,
            "confirm_not_duplicate": True,
        }
    )
    assert form.is_valid(), form.errors


def test_create_endpoint_returns_409_with_candidates(client, district):
    existing = make_org(name="Kisan Unnati Farmer Producer Company Limited", district=district)

    response = client.post(
        reverse("organisation-list"),
        {"name": "Kisan Unnati FPC Ltd", "type": OrgType.FPO, "district": district.pk},
        format="json",
    )

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "conflict"
    candidates = body["details"]["candidates"]
    assert [c["id"] for c in candidates] == [str(existing.pk)]
    assert candidates[0]["district"] == "Muzaffarnagar"
    assert candidates[0]["similarity"] >= 0.6
    assert Organisation.objects.filter(name="Kisan Unnati FPC Ltd").count() == 0


def test_force_creates_and_records_who_overrode(client, district, data_ops):
    make_org(name="Kisan Unnati Farmer Producer Company Limited", district=district)

    response = client.post(
        reverse("organisation-list") + "?force=true",
        {"name": "Kisan Unnati FPC Ltd", "type": OrgType.FPO, "district": district.pk},
        format="json",
    )

    assert response.status_code == 201
    created = Organisation.objects.get(name="Kisan Unnati FPC Ltd")
    assert created.extra["duplicate_override"]["by"] == str(data_ops.public_id)
    assert created.created_by == data_ops


def test_check_duplicates_endpoint_matches_the_create_path(client, district):
    make_org(name="Kisan Unnati Farmer Producer Company Limited", district=district)

    response = client.post(
        reverse("organisation-check-duplicates"),
        {"name": "Kisan Unnati FPC Ltd", "district": district.pk},
        format="json",
    )
    assert response.status_code == 200
    assert len(response.json()["candidates"]) == 1


def test_create_succeeds_when_nothing_is_similar(client, district):
    response = client.post(
        reverse("organisation-list"),
        {"name": "Rohana Kalan Sugar Mills", "type": OrgType.SUGAR_MILL, "district": district.pk},
        format="json",
    )
    assert response.status_code == 201, response.json()


# ---------------------------------------------------------------------------
# Nothing is hard-deleted
# ---------------------------------------------------------------------------


def test_delete_is_a_soft_delete(client, district):
    org = make_org(district=district)

    response = client.delete(reverse("organisation-detail", args=[org.pk]))
    assert response.status_code == 204

    org.refresh_from_db()
    assert org.is_deleted is True
    assert org.status == OrgStatus.DEFUNCT
    # 🔴 The row survives: merges, provenance and the audit trail all point at
    # this id and must continue to resolve.
    assert Organisation.objects.filter(pk=org.pk).exists()
    assert client.get(reverse("organisation-detail", args=[org.pk])).status_code == 200


def test_soft_deleted_rows_leave_the_default_list(client, district):
    org = make_org(district=district)
    client.delete(reverse("organisation-detail", args=[org.pk]))

    listed = client.get(reverse("organisation-list")).json()["results"]
    assert [row["id"] for row in listed] == []

    including = client.get(reverse("organisation-list") + "?include_deleted=true").json()
    assert [row["id"] for row in including["results"]] == [str(org.pk)]


# ---------------------------------------------------------------------------
# Listing and filtering
# ---------------------------------------------------------------------------


def test_list_filters_and_alias_search(client, district, other_district):
    make_org(name="Rohana Kalan Sugar Mills", type=OrgType.SUGAR_MILL, district=district)
    make_org(
        name="Bhainswal Kisan Producer Company Limited",
        aliases=["Bhainswal FPC"],
        district=other_district,
    )

    mills = client.get(reverse("organisation-list") + f"?type={OrgType.SUGAR_MILL}").json()
    assert [row["name"] for row in mills["results"]] == ["Rohana Kalan Sugar Mills"]

    by_district = client.get(reverse("organisation-list") + f"?district={district.pk}").json()
    assert len(by_district["results"]) == 1

    # Aliases are a text[]; the search has to flatten it or the other spellings
    # are invisible, which is how analysts end up creating the duplicate.
    by_alias = client.get(reverse("organisation-list") + "?q=Bhainswal FPC").json()
    assert [row["name"] for row in by_alias["results"]] == [
        "Bhainswal Kisan Producer Company Limited"
    ]


def test_unknown_filter_is_rejected_rather_than_ignored(client):
    """
    A typo'd filter that silently does nothing is how somebody exports the
    whole registry believing they exported one district.
    """
    response = client.get(reverse("organisation-list") + "?quality_teir=gold")
    assert response.status_code == 400
    assert "quality_teir" in str(response.json())


def test_villages_refuse_an_unscoped_list(client, district):
    block = Block.objects.create(district=district, name="Budhana")
    Village.objects.create(block=block, district=district, name="Bhainswal")

    assert client.get(reverse("village-list")).status_code == 400

    scoped = client.get(reverse("village-list") + f"?district={district.pk}").json()
    assert [row["name"] for row in scoped["results"]] == ["Bhainswal"]


# ---------------------------------------------------------------------------
# 🔴 Source register — R1 and R4
# ---------------------------------------------------------------------------


@pytest.mark.compliance
def test_r4_pii_may_not_enter_through_a_public_registry():
    """
    R4 (Doc 05 §5): personal data enters only via partner agreement, field
    collection, inbound signup, or an approved Theta / licensed batch.
    """
    form = SourceAdminForm(
        data={
            "code": "some_state_portal",
            "name": "A state subsidy portal",
            "kind": SourceKind.PUBLIC_REGISTRY,
            "legal_basis": "Published on a government website.",
            "contains_pii": True,
            "is_approved": False,
        }
    )
    assert not form.is_valid()
    assert "contains_pii" in form.errors


@pytest.mark.compliance
def test_r4_permits_a_partner_agreement():
    form = SourceAdminForm(
        data={
            "code": "fpo_mou_bhainswal",
            "name": "Bhainswal FPO MoU member list",
            "kind": SourceKind.PARTNER_AGREEMENT,
            "legal_basis": "MoU clause 7, consent captured at registration.",
            "contains_pii": True,
            "is_approved": True,
        }
    )
    assert form.is_valid(), form.errors


@pytest.mark.compliance
def test_a_source_cannot_be_approved_without_a_legal_basis():
    """R1's precondition: approval is a written justification, not a checkbox."""
    form = SourceAdminForm(
        data={
            "code": "mystery_list",
            "name": "A list someone found",
            "kind": SourceKind.PUBLIC_REGISTRY,
            "legal_basis": "   ",
            "contains_pii": False,
            "is_approved": True,
        }
    )
    assert not form.is_valid()
    assert "legal_basis" in form.errors


def test_a_new_source_defaults_to_unapproved():
    """🔴 R1 fails closed. A source arrives unusable until a human approves it."""
    source = Source.objects.create(
        code="up_cane_commissioner_portal",
        name="UP Cane Commissioner mill directory",
        kind=SourceKind.PUBLIC_REGISTRY,
        legal_basis="State-published directory of licensed sugar mills. Institutional data.",
    )
    assert source.is_approved is False
    assert source.approved_by is None
    assert "not approved" in str(source)


@pytest.mark.compliance
def test_the_seeded_source_register_holds_the_doc_05_position():
    """
    🔴 R4, asserted against the register the project actually ships with.

    Doc 05's settled position in two clauses. Institutional data — MCA, SFAC,
    LGD, the trade directories — is fair game and carries no personal data.
    Personal data exists in the register, because partner agreements, field
    collection and inbound signups are exactly how it is *supposed* to arrive;
    what must never appear is a PII-bearing source of any other kind.

    A seed that gained one would be a compliance change disguised as a data
    change, which is the kind that gets merged without review.
    """
    approved = Source.objects.filter(is_approved=True)
    assert approved.count() >= 5

    # Every source, approved or not, uses a lawful route for what it holds.
    unlawful = [s.code for s in Source.objects.all() if not s.pii_route_is_lawful]
    assert unlawful == []

    # No registry, directory or open-data source claims personal data.
    institutional = Source.objects.filter(
        kind__in=[
            SourceKind.PUBLIC_REGISTRY,
            SourceKind.OPEN_GOVERNMENT_DATA,
            SourceKind.INDUSTRY_DIRECTORY,
            SourceKind.OFFICIAL_WEBSITE,
        ]
    )
    assert institutional.exists()
    assert not institutional.filter(contains_pii=True).exists()

    # An approval with no written justification is not an approval.
    assert all(source.legal_basis.strip() for source in approved)


def test_unknown_organisation_id_is_a_404(client):
    assert client.get(reverse("organisation-detail", args=[uuid.uuid4()])).status_code == 404


# ---------------------------------------------------------------------------
# The admin renders
#
# Fieldsets naming a field that does not exist, an autocomplete pointing at an
# unregistered model, an inline with the wrong fk_name: all of these import
# cleanly and raise on the first page load. Since the admin *is* the Phase 1
# data-ops console, "it renders" is a functional requirement, not a nicety.
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_http():
    from django.test import Client

    User.objects.create_superuser(
        email="admin@thetaanalytics.in", password=PASSWORD, full_name="System Admin"
    )
    http = Client()
    http.force_login(User.objects.get(email="admin@thetaanalytics.in"))
    return http


@pytest.mark.parametrize(
    "url",
    [
        "/admin/organisations/organisation/",
        "/admin/organisations/organisation/add/",
        "/admin/dataquality/source/",
        "/admin/dataquality/fieldprovenance/",
        "/admin/dataquality/contradiction/",
        "/admin/geography/state/",
        "/admin/geography/district/",
        "/admin/geography/block/",
        "/admin/geography/village/",
        "/admin/geography/crop/",
    ],
)
def test_admin_pages_render(admin_http, url):
    assert admin_http.get(url).status_code == 200


def test_admin_change_page_shows_only_the_matching_type_profile(admin_http, district):
    mill = make_org(name="Rohana Kalan Sugar Mills", type=OrgType.SUGAR_MILL, district=district)
    SugarMillProfile.objects.create(organisation=mill, crushing_capacity_tcd=7500)

    page = admin_http.get(f"/admin/organisations/organisation/{mill.pk}/change/")
    body = page.content.decode()
    assert "crushing_capacity_tcd" in body
    # An FPO's fields have no business on a mill's form.
    assert "shareholder_count" not in body


def test_admin_village_list_refuses_to_scan(admin_http, district):
    """🔴 660k rows. The changelist stays empty until it is narrowed."""
    block = Block.objects.create(district=district, name="Budhana")
    Village.objects.create(block=block, district=district, name="Bhainswal")

    unfiltered = admin_http.get("/admin/geography/village/")
    assert unfiltered.status_code == 200
    assert list(unfiltered.context["cl"].queryset) == []

    narrowed = admin_http.get(f"/admin/geography/village/?district__state__id__exact={UP_STATE_ID}")
    assert [v.name for v in narrowed.context["cl"].queryset] == ["Bhainswal"]


def test_admin_soft_delete_action_leaves_the_row(admin_http, district):
    org = make_org(district=district)

    response = admin_http.post(
        "/admin/organisations/organisation/",
        {"action": "soft_delete", "_selected_action": [str(org.pk)], "index": 0},
        follow=True,
    )
    assert response.status_code == 200

    org.refresh_from_db()
    assert org.is_deleted is True
    assert Organisation.objects.filter(pk=org.pk).exists()


@pytest.mark.compliance
def test_admin_stamps_the_approver_on_a_source(admin_http):
    """🔴 R1: approval is a named human act, stamped from the request."""
    source = Source.objects.get(code="lgd_directory")
    Source.objects.filter(pk=source.pk).update(
        is_approved=False, approved_by=None, approved_at=None
    )

    response = admin_http.post(
        f"/admin/dataquality/source/{source.pk}/change/",
        {
            "code": source.code,
            "name": source.name,
            "kind": source.kind,
            "url": source.url or "",
            "legal_basis": source.legal_basis,
            "licence": "",
            "refresh_cadence": "",
            "notes": "",
            "is_approved": "on",
        },
    )
    assert response.status_code == 302, response.context["adminform"].form.errors

    source.refresh_from_db()
    assert source.is_approved is True
    assert "admin@thetaanalytics.in" in source.approved_by
    assert source.approved_at is not None
