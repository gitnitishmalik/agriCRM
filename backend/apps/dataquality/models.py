"""
`dq` — provenance and the source register.

🔴 `dq.source` is not a lookup table. It is the compliance record that answers
"why are we allowed to hold this?" for every row in the database, and R1
(Doc 05 §5) makes it load-bearing: a collector asserts
`source.is_approved` before its first request and exits non-zero if it is
false. `legal_basis` is `NOT NULL` in the DDL for the same reason — a source
nobody can write a sentence of justification for is a source nobody approves.

`is_approved` is deliberately not a property of the code that reads it.
Approval is a human act, recorded with a name and a timestamp, and revocable
without a deploy. Track P2 sets it batch by batch for Theta's legacy data,
never wholesale.

Everything here is `managed = False`; `sql/schema.sql` owns the DDL.
"""

from __future__ import annotations

from django.db import models

from config.db import schema_table


class QualityTier(models.TextChoices):
    """
    `dq.quality_tier` — Doc 07 §2.

    Order is best to worst, which is also the order the admin filters render
    in. Bronze is a lead, not a fact, and is never messaged; quarantine is
    excluded from search, campaigns and counts but never silently deleted.
    """

    GOLD = "gold", "Gold"
    SILVER = "silver", "Silver"
    BRONZE = "bronze", "Bronze"
    QUARANTINE = "quarantine", "Quarantine"


class SourceKind(models.TextChoices):
    """`dq.source_kind`."""

    PUBLIC_REGISTRY = "public_registry", "Public registry"
    OPEN_GOVERNMENT_DATA = "open_government_data", "Open government data"
    OFFICIAL_WEBSITE = "official_website", "Official website"
    INDUSTRY_DIRECTORY = "industry_directory", "Industry directory"
    PARTNER_AGREEMENT = "partner_agreement", "Partner agreement"
    FIELD_COLLECTION = "field_collection", "Field collection"
    INBOUND_SIGNUP = "inbound_signup", "Inbound signup"
    THETA_ANALYTICS = "theta_analytics", "Theta Analytics legacy"
    PURCHASED_LICENSED = "purchased_licensed", "Purchased / licensed"
    MANUAL_ENTRY = "manual_entry", "Manual entry"
    INFERRED = "inferred", "Inferred"
    UNKNOWN = "unknown", "Unknown"


#: 🔴 R4 (Doc 05 §5) — the only lawful routes for personal data. A source of
#: any other kind must not be marked `contains_pii`.
PII_PERMITTED_SOURCE_KINDS = frozenset(
    {
        SourceKind.PARTNER_AGREEMENT,
        SourceKind.FIELD_COLLECTION,
        SourceKind.INBOUND_SIGNUP,
        SourceKind.THETA_ANALYTICS,
        SourceKind.PURCHASED_LICENSED,
    }
)


class Source(models.Model):
    id = models.AutoField(primary_key=True)
    code = models.TextField(unique=True, help_text="Stable identifier used by collector code.")
    name = models.TextField()
    kind = models.CharField(max_length=32, choices=SourceKind.choices)
    url = models.TextField(null=True, blank=True)
    legal_basis = models.TextField(
        help_text="🔴 Mandatory. Why we may hold data from this source. "
        "A source with no written legal basis must not be approved."
    )
    licence = models.TextField(null=True, blank=True)
    contains_pii = models.BooleanField(default=False)
    is_approved = models.BooleanField(
        default=False,
        help_text="🔴 R1. Collectors refuse to run against an unapproved source.",
    )
    approved_by = models.TextField(null=True, blank=True, help_text="A named human, not a system.")
    approved_at = models.DateTimeField(null=True, blank=True)
    refresh_cadence = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = schema_table("dq", "source")
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} ({'approved' if self.is_approved else 'not approved'})"

    @property
    def pii_route_is_lawful(self) -> bool:
        """
        🔴 R4. False means this source is claiming to carry personal data
        through a route Doc 05 does not permit. The admin refuses to save it
        and `collectors.base` refuses to run it.
        """
        return not self.contains_pii or self.kind in PII_PERMITTED_SOURCE_KINDS


class FieldProvenance(models.Model):
    """
    Sparse by design — one row per field we care to defend, not per column.

    `confidence` is what stops a bulk import overwriting a human. Field
    verification is 0.95, a scraped registry 0.60, and the upsert rule requires
    incoming confidence to beat the existing value by more than 0.15 before it
    writes; otherwise it raises a contradiction instead.
    """

    id = models.BigAutoField(primary_key=True)
    entity_type = models.TextField()
    entity_id = models.UUIDField()
    field_name = models.TextField()
    value_text = models.TextField(null=True, blank=True)
    source = models.ForeignKey(
        Source, on_delete=models.DO_NOTHING, db_column="source_id", related_name="provenance"
    )
    source_reference = models.TextField(
        null=True, blank=True, help_text="URL, file name, row number, MoU id."
    )
    confidence = models.DecimalField(max_digits=3, decimal_places=2, default=0.50)
    collected_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.UUIDField(null=True, blank=True)
    is_current = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = schema_table("dq", "field_provenance")
        ordering = ["-collected_at"]
        verbose_name_plural = "field provenance"

    def __str__(self) -> str:
        return f"{self.entity_type}.{self.field_name} ← {self.source_id}"


class Contradiction(models.Model):
    """Two sources disagree. An analyst resolves it; nothing resolves itself."""

    id = models.BigAutoField(primary_key=True)
    entity_type = models.TextField()
    entity_id = models.UUIDField()
    field_name = models.TextField()
    value_a = models.TextField(null=True, blank=True)
    value_b = models.TextField(null=True, blank=True)
    provenance_a = models.ForeignKey(
        FieldProvenance,
        on_delete=models.DO_NOTHING,
        db_column="provenance_a",
        related_name="+",
        null=True,
        blank=True,
    )
    provenance_b = models.ForeignKey(
        FieldProvenance,
        on_delete=models.DO_NOTHING,
        db_column="provenance_b",
        related_name="+",
        null=True,
        blank=True,
    )
    detected_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.UUIDField(null=True, blank=True)
    resolution = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = schema_table("dq", "contradiction")
        ordering = ["-detected_at"]

    def __str__(self) -> str:
        state = "open" if self.resolved_at is None else "resolved"
        return f"{self.entity_type}.{self.field_name} — {state}"
