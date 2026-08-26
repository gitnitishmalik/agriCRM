"""
`core.organisation` — FPOs, sugar mills, cooperative societies and everyone
else the commercial layer sells to.

**One table with a type discriminator, plus profile extensions.** Three
separate tables would triple every join, every search and every permission
rule, and the moment a cooperative sugar mill needs to be both a mill and a
society you would be maintaining two rows for one thing. What differs between
types is roughly a dozen fields each, so those live in `fpo_profile`,
`sugar_mill_profile` and `cooperative_profile` — one-to-one, created only when
there is something to put in them.

Nothing here is hard-deleted. `is_deleted` hides a row, `merged_into_id`
points at the survivor of a merge, and `dq.merge_event` holds a JSONB snapshot
so a merge reverses. Use `Organisation.live` for anything user-facing and
`Organisation.objects` when you genuinely need to see the tombstones —
including in the admin, because an org you cannot see is an org you cannot
un-delete.

`managed = False`: `sql/schema.sql` owns the DDL, including the constraints
the ORM cannot express.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.dataquality.models import QualityTier, Source
from apps.geography.models import Block, District, State, Village
from config.db import schema_table


class OrgType(models.TextChoices):
    """`core.org_type`."""

    FPO = "fpo", "FPO (Farmer Producer Organisation)"
    ACS = "acs", "Cooperative society / PACS"
    SUGAR_MILL = "sugar_mill", "Sugar mill"
    COOPERATIVE_FEDERATION = "cooperative_federation", "Cooperative federation"
    INPUT_DEALER = "input_dealer", "Input dealer"
    NGO_PROMOTING_INSTITUTION = "ngo_promoting_institution", "NGO / promoting institution"
    GOVERNMENT_BODY = "government_body", "Government body"
    PRIVATE_COMPANY = "private_company", "Private company"
    BANK_NBFC = "bank_nbfc", "Bank / NBFC"
    OTHER = "other", "Other"


class OrgStatus(models.TextChoices):
    """`core.org_status`."""

    PROSPECT = "prospect", "Prospect"
    ACTIVE = "active", "Active"
    DORMANT = "dormant", "Dormant"
    DEFUNCT = "defunct", "Defunct"
    MERGED = "merged", "Merged"
    BLACKLISTED = "blacklisted", "Blacklisted"


class LegalForm(models.TextChoices):
    """`core.legal_form`."""

    PRODUCER_COMPANY = "producer_company", "Producer company"
    COOPERATIVE_SOCIETY = "cooperative_society", "Cooperative society"
    SECTION_8_COMPANY = "section_8_company", "Section 8 company"
    PRIVATE_LIMITED = "private_limited", "Private limited"
    PUBLIC_LIMITED = "public_limited", "Public limited"
    LLP = "llp", "LLP"
    PARTNERSHIP = "partnership", "Partnership"
    PROPRIETORSHIP = "proprietorship", "Proprietorship"
    TRUST = "trust", "Trust"
    SOCIETY = "society", "Society"
    STATUTORY_BODY = "statutory_body", "Statutory body"
    UNREGISTERED = "unregistered", "Unregistered"
    UNKNOWN = "unknown", "Unknown"


class MillOwnership(models.TextChoices):
    """`core.mill_ownership`."""

    PRIVATE = "private", "Private"
    COOPERATIVE = "cooperative", "Cooperative"
    PUBLIC_SECTOR = "public_sector", "Public sector"
    JOINT_SECTOR = "joint_sector", "Joint sector"


def _user_fk(db_column: str, related_name: str) -> models.ForeignKey:
    """
    A reference to `accounts.User` that joins on `User.public_id`.

    `db_constraint=False` because the DDL declares these columns as bare
    `uuid` with no foreign key: the business schema deliberately does not
    depend on Django's auth tables, so removing a user never cascades into
    commercial history.
    """
    return models.ForeignKey(
        settings.AUTH_USER_MODEL,
        to_field="public_id",
        db_column=db_column,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        related_name=related_name,
        null=True,
        blank=True,
    )


class LiveOrganisationManager(models.Manager):
    """Everything that is not a tombstone. The default for user-facing reads."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class Organisation(models.Model):
    # The DDL defaults this to uuid_generate_v4(); Django will not omit a
    # non-auto primary key from an INSERT, so the default is mirrored here.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_code = models.TextField(
        unique=True, null=True, blank=True, help_text="Human-friendly: FPO-UP-000123"
    )
    type = models.CharField(max_length=32, choices=OrgType.choices)
    status = models.CharField(max_length=16, choices=OrgStatus.choices, default=OrgStatus.PROSPECT)
    legal_form = models.CharField(
        max_length=32, choices=LegalForm.choices, default=LegalForm.UNKNOWN
    )

    name = models.TextField()
    name_local = models.TextField(null=True, blank=True, help_text="Devanagari, kept verbatim.")
    short_name = models.TextField(null=True, blank=True)
    aliases = ArrayField(
        models.TextField(),
        default=list,
        blank=True,
        help_text="Every other spelling this org is known by. Matching reads these.",
    )

    # Statutory identifiers. Business data, not personal data — MCA publishes
    # CINs by statute, which is why collecting them is fair game (Doc 05).
    cin = models.CharField(max_length=21, null=True, blank=True, verbose_name="CIN")
    registration_no = models.TextField(null=True, blank=True)
    registration_act = models.TextField(null=True, blank=True)
    registration_date = models.DateField(null=True, blank=True)
    pan_masked = models.CharField(
        max_length=14,
        null=True,
        blank=True,
        verbose_name="PAN (masked)",
        help_text="Masked only: ABCDE****F. A full PAN never enters this column.",
    )
    gstin = models.CharField(max_length=15, null=True, blank=True, verbose_name="GSTIN")
    udyam_no = models.TextField(null=True, blank=True)

    state = models.ForeignKey(
        State,
        on_delete=models.DO_NOTHING,
        db_column="state_id",
        related_name="organisations",
        null=True,
        blank=True,
    )
    district = models.ForeignKey(
        District,
        on_delete=models.DO_NOTHING,
        db_column="district_id",
        related_name="organisations",
        null=True,
        blank=True,
    )
    block = models.ForeignKey(
        Block,
        on_delete=models.DO_NOTHING,
        db_column="block_id",
        related_name="organisations",
        null=True,
        blank=True,
    )
    village = models.ForeignKey(
        Village,
        on_delete=models.DO_NOTHING,
        db_column="village_id",
        related_name="organisations",
        null=True,
        blank=True,
    )
    address_line1 = models.TextField(null=True, blank=True)
    address_line2 = models.TextField(null=True, blank=True)
    pincode = models.CharField(max_length=6, null=True, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    website = models.TextField(null=True, blank=True)
    established_year = models.SmallIntegerField(null=True, blank=True)
    member_count = models.IntegerField(null=True, blank=True)
    women_member_count = models.IntegerField(null=True, blank=True)
    annual_turnover_inr = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True
    )
    turnover_fy = models.CharField(max_length=7, null=True, blank=True, help_text="e.g. 2024-25")

    parent_org = models.ForeignKey(
        "self",
        on_delete=models.DO_NOTHING,
        db_column="parent_org_id",
        related_name="subsidiaries",
        null=True,
        blank=True,
    )
    promoting_agency = models.TextField(
        null=True, blank=True, help_text="NABARD, SFAC, NCDC, state department"
    )
    scheme_reference = models.TextField(
        null=True, blank=True, help_text="e.g. 10,000 FPO scheme cluster id"
    )

    quality_tier = models.CharField(
        max_length=16, choices=QualityTier.choices, default=QualityTier.BRONZE
    )
    completeness_score = models.SmallIntegerField(default=0)
    primary_source = models.ForeignKey(
        Source,
        on_delete=models.DO_NOTHING,
        db_column="primary_source_id",
        related_name="organisations",
        null=True,
        blank=True,
    )

    owner_user = _user_fk("owner_user_id", "owned_organisations")
    tags = ArrayField(models.TextField(), default=list, blank=True)
    extra = models.JSONField(default=dict, blank=True)

    merged_into = models.ForeignKey(
        "self",
        on_delete=models.DO_NOTHING,
        db_column="merged_into_id",
        related_name="merged_from",
        null=True,
        blank=True,
    )
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = _user_fk("created_by", "+")
    updated_by = _user_fk("updated_by", "+")

    objects = models.Manager()
    live = LiveOrganisationManager()

    class Meta:
        managed = False
        db_table = schema_table("core", "organisation")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_merged_away(self) -> bool:
        return self.merged_into_id is not None


class FpoProfile(models.Model):
    """Type extension for `OrgType.FPO`. Doc 08 §2."""

    organisation = models.OneToOneField(
        Organisation,
        on_delete=models.DO_NOTHING,
        db_column="organisation_id",
        primary_key=True,
        related_name="fpo_profile",
    )
    authorised_capital = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    paid_up_capital = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    share_value_inr = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    shareholder_count = models.IntegerField(null=True, blank=True)
    business_lines = ArrayField(
        models.TextField(),
        default=list,
        blank=True,
        help_text="input sale, output aggregation, custom hiring, processing",
    )
    licences = ArrayField(
        models.TextField(),
        default=list,
        blank=True,
        help_text="seed, fertiliser, pesticide, FSSAI, mandi",
    )
    has_storage = models.BooleanField(null=True, blank=True)
    storage_capacity_mt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    has_processing_unit = models.BooleanField(null=True, blank=True)
    processing_details = models.TextField(null=True, blank=True)
    custom_hiring_centre = models.BooleanField(null=True, blank=True)
    equity_grant_received = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    credit_guarantee = models.BooleanField(null=True, blank=True)
    cbbo_name = models.TextField(
        null=True,
        blank=True,
        verbose_name="CBBO",
        help_text="Cluster Based Business Organisation",
    )
    implementing_agency = models.TextField(
        null=True, blank=True, help_text="SFAC / NABARD / NCDC / NAFED"
    )
    last_agm_date = models.DateField(null=True, blank=True, verbose_name="Last AGM date")
    last_annual_return_fy = models.CharField(max_length=7, null=True, blank=True)
    primary_crops = ArrayField(
        models.IntegerField(), default=list, blank=True, help_text="ref.crop ids"
    )

    class Meta:
        managed = False
        db_table = schema_table("core", "fpo_profile")
        verbose_name = "FPO profile"

    def __str__(self) -> str:
        return f"FPO profile — {self.organisation_id}"


class SugarMillProfile(models.Model):
    """
    Type extension for `OrgType.SUGAR_MILL`. Doc 08 §3.

    `season_start_month` / `season_end_month` are not decoration: mill
    decision-makers are unreachable during crushing, roughly November to
    April, and available May to September. The BD calendar is built from these two
    columns.
    """

    organisation = models.OneToOneField(
        Organisation,
        on_delete=models.DO_NOTHING,
        db_column="organisation_id",
        primary_key=True,
        related_name="sugar_mill_profile",
    )
    ownership = models.CharField(
        max_length=16, choices=MillOwnership.choices, default=MillOwnership.PRIVATE
    )
    crushing_capacity_tcd = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Crushing capacity (TCD)",
        help_text="Tonnes of cane per day.",
    )
    installed_year = models.SmallIntegerField(null=True, blank=True)
    cogeneration_mw = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True, verbose_name="Cogeneration (MW)"
    )
    distillery_capacity_klpd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Distillery capacity (KLPD)",
    )
    has_ethanol_plant = models.BooleanField(default=False)
    has_cbg_plant = models.BooleanField(default=False, verbose_name="Has CBG plant")
    refinery_capacity_tpd = models.IntegerField(
        null=True, blank=True, verbose_name="Refinery capacity (TPD)"
    )
    cane_command_villages = models.IntegerField(null=True, blank=True)
    registered_cane_growers = models.IntegerField(null=True, blank=True)
    avg_recovery_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Avg recovery %"
    )
    cane_price_srp_inr = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Cane price (SAP)",
    )
    season_start_month = models.SmallIntegerField(null=True, blank=True)
    season_end_month = models.SmallIntegerField(null=True, blank=True)
    is_operational = models.BooleanField(default=True)
    federation_membership = ArrayField(
        models.TextField(), default=list, blank=True, help_text="ISMA, NFCSF, state sugarfed"
    )
    cane_payment_status = models.TextField(
        null=True, blank=True, help_text="'current' or 'arrears'"
    )
    cane_arrears_inr_cr = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Cane arrears (INR cr)",
    )

    class Meta:
        managed = False
        db_table = schema_table("core", "sugar_mill_profile")
        verbose_name = "sugar mill profile"

    def __str__(self) -> str:
        return f"Mill profile — {self.organisation_id}"


class CooperativeProfile(models.Model):
    """Type extension for `OrgType.ACS`. Doc 08 §4."""

    organisation = models.OneToOneField(
        Organisation,
        on_delete=models.DO_NOTHING,
        db_column="organisation_id",
        primary_key=True,
        related_name="cooperative_profile",
    )
    society_type = models.TextField(
        null=True,
        blank=True,
        help_text="PACS, cane society, dairy, marketing, credit, multipurpose",
    )
    registration_act = models.TextField(null=True, blank=True)
    affiliated_to_org = models.ForeignKey(
        Organisation,
        on_delete=models.DO_NOTHING,
        db_column="affiliated_to_org_id",
        related_name="affiliated_societies",
        null=True,
        blank=True,
    )
    is_pacs = models.BooleanField(default=False, verbose_name="Is PACS")
    is_computerised = models.BooleanField(
        null=True, blank=True, help_text="PACS computerisation scheme"
    )
    deposit_base_inr = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    loan_outstanding_inr = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True
    )
    area_of_operation = models.TextField(null=True, blank=True)
    villages_covered = models.IntegerField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = schema_table("core", "cooperative_profile")
        verbose_name = "cooperative profile"

    def __str__(self) -> str:
        return f"Cooperative profile — {self.organisation_id}"


class OrgAnnualMetric(models.Model):
    """
    Year-keyed facts: cane crushed, sugar produced, turnover.

    Kept out of `organisation` because these arrive per financial year from a
    different source than the registry row, and overwriting last year's number
    with this year's is how a trend disappears.
    """

    id = models.BigAutoField(primary_key=True)
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.DO_NOTHING,
        db_column="organisation_id",
        related_name="annual_metrics",
    )
    fy = models.CharField(max_length=7, verbose_name="FY", help_text="e.g. 2024-25")
    metric_code = models.TextField(
        help_text="cane_crushed_lmt, sugar_produced_lmt, turnover_inr, ..."
    )
    metric_value = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    unit = models.TextField(null=True, blank=True)
    source = models.ForeignKey(
        Source,
        on_delete=models.DO_NOTHING,
        db_column="source_id",
        related_name="org_metrics",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = schema_table("core", "org_annual_metric")
        ordering = ["-fy", "metric_code"]
        verbose_name = "organisation annual metric"
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "fy", "metric_code"], name="uq_orgmetric"
            )
        ]

    def __str__(self) -> str:
        return f"{self.metric_code} {self.fy}: {self.metric_value}"
