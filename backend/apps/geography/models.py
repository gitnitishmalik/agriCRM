"""
`ref` — LGD-aligned geography and crop reference data.

🔴 This is the first thing Phase 1 loads and everything else joins to it
(Doc 15, Phase 1 sprint order). A district row that arrives after the
organisations referencing it is a district that got created by hand, with a
name spelled the way one analyst spells it, and the LGD code left null.

**LGD codes are the join key to the outside world**, not our surrogate ids.
Every government dataset — SFAC's FPO list, PMFBY, the state sugarfed
directories — keys on LGD. `lgd_code` is therefore unique and nullable rather
than the primary key: unique because two rows claiming one code means an
import went wrong, nullable because a village we learn about from a partner
MoU before it appears in an LGD extract still has to exist.

These models are `managed = False`. Django will not create, alter or drop
these tables; `sql/schema.sql` does. See `config.db`.
"""

from __future__ import annotations

from django.db import models

from config.db import schema_table


class Season(models.TextChoices):
    """`core.season`."""

    KHARIF = "kharif", "Kharif"
    RABI = "rabi", "Rabi"
    ZAID = "zaid", "Zaid"
    PERENNIAL = "perennial", "Perennial"
    ANNUAL = "annual", "Annual"


class State(models.Model):
    id = models.SmallIntegerField(primary_key=True)
    lgd_code = models.IntegerField(unique=True, null=True, blank=True)
    name = models.TextField(unique=True)
    name_local = models.TextField(null=True, blank=True)
    iso_code = models.TextField(null=True, blank=True)
    is_ut = models.BooleanField(default=False, verbose_name="Union Territory")

    class Meta:
        managed = False
        db_table = schema_table("ref", "state")
        ordering = ["name"]
        verbose_name = "state / UT"
        verbose_name_plural = "states / UTs"

    def __str__(self) -> str:
        return self.name


class District(models.Model):
    id = models.AutoField(primary_key=True)
    lgd_code = models.IntegerField(unique=True, null=True, blank=True)
    state = models.ForeignKey(
        State, on_delete=models.DO_NOTHING, db_column="state_id", related_name="districts"
    )
    name = models.TextField()
    name_local = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = schema_table("ref", "district")
        ordering = ["state__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["state", "name"], name="uq_district_state_name")
        ]

    def __str__(self) -> str:
        return f"{self.name}, {self.state_id and self.state.name}"


class Block(models.Model):
    id = models.AutoField(primary_key=True)
    lgd_code = models.IntegerField(unique=True, null=True, blank=True)
    district = models.ForeignKey(
        District, on_delete=models.DO_NOTHING, db_column="district_id", related_name="blocks"
    )
    name = models.TextField()
    name_local = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = schema_table("ref", "block")
        ordering = ["name"]
        verbose_name = "block / tehsil"
        constraints = [
            models.UniqueConstraint(fields=["district", "name"], name="uq_block_district_name")
        ]

    def __str__(self) -> str:
        return self.name


class Village(models.Model):
    """
    ~660k rows once LGD is fully loaded. Nothing here should ever be listed
    unfiltered — the admin and the API both require a district first.
    """

    id = models.BigAutoField(primary_key=True)
    lgd_code = models.IntegerField(unique=True, null=True, blank=True)
    block = models.ForeignKey(
        Block,
        on_delete=models.DO_NOTHING,
        db_column="block_id",
        related_name="villages",
        null=True,
        blank=True,
    )
    # Denormalised deliberately: a village whose block is unknown still has to
    # be placed on the map, and every territory rule in Doc 12 is keyed on
    # district, so this join must never be optional.
    district = models.ForeignKey(
        District, on_delete=models.DO_NOTHING, db_column="district_id", related_name="villages"
    )
    name = models.TextField()
    name_local = models.TextField(null=True, blank=True)
    pincode = models.CharField(max_length=6, null=True, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        managed = False
        db_table = schema_table("ref", "village")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["district", "block", "name"], name="uq_village_district_block_name"
            )
        ]

    def __str__(self) -> str:
        return self.name


class Crop(models.Model):
    id = models.AutoField(primary_key=True)
    code = models.TextField(unique=True)
    name = models.TextField()
    name_local = models.TextField(null=True, blank=True)
    category = models.TextField(
        null=True,
        blank=True,
        help_text="cereal, pulse, oilseed, cash, horticulture, fodder",
    )
    default_season = models.CharField(max_length=16, choices=Season.choices, null=True, blank=True)

    class Meta:
        managed = False
        db_table = schema_table("ref", "crop")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CropVariety(models.Model):
    id = models.AutoField(primary_key=True)
    crop = models.ForeignKey(
        Crop, on_delete=models.DO_NOTHING, db_column="crop_id", related_name="varieties"
    )
    name = models.TextField()
    maturity_days = models.SmallIntegerField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = schema_table("ref", "crop_variety")
        ordering = ["crop__name", "name"]
        verbose_name_plural = "crop varieties"
        constraints = [
            models.UniqueConstraint(fields=["crop", "name"], name="uq_variety_crop_name")
        ]

    def __str__(self) -> str:
        return f"{self.crop.name} — {self.name}"
