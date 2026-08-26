"""
Organisation admin — the Phase 1 data-ops console.

This is not a fallback UI. Doc 15's exit gate is "data-ops team working in
Django Admin **daily** — not a demo, actual use", and Doc 03 §11 budgets it as
roughly three months of frontend work that we get on day one. The screens here
are built for the loop an analyst actually runs: search by a half-remembered
name, see the geography and the quality tier without opening the record, fix
one field, move on.

Three things are deliberate and should survive future edits:

* **Creating a duplicate is blocked, not warned about.** See `dedupe`. The
  override is a checkbox with the candidates listed next to it, so saying
  "yes, these are different" is a recorded decision rather than a reflex.
* **Nothing is hard-deleted.** The delete permission is off everywhere; the
  actions soft-delete and restore instead. A tombstone is visible in the
  changelist because an organisation nobody can see is one nobody can undo.
* **Profile inlines follow the type discriminator.** An FPO does not get a
  crushing-capacity field, and a mill does not get a shareholder count.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.db.models import F, Func, TextField, Value
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from .dedupe import find_duplicates
from .models import (
    CooperativeProfile,
    FpoProfile,
    Organisation,
    OrgAnnualMetric,
    OrgStatus,
    OrgType,
    SugarMillProfile,
)


class OrganisationAdminForm(forms.ModelForm):
    """Blocks a save that would create a duplicate inside one district."""

    confirm_not_duplicate = forms.BooleanField(
        required=False,
        label=_("These are genuinely different organisations"),
        help_text=_(
            "Only tick this after checking the matches listed above. "
            "Two records for one FPO cost more to unpick later than they save now."
        ),
    )

    class Meta:
        model = Organisation
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        name = cleaned.get("name")
        if not name or cleaned.get("confirm_not_duplicate"):
            return cleaned

        district = cleaned.get("district")
        state = cleaned.get("state")
        matches = find_duplicates(
            name,
            district_id=district.pk if district else None,
            state_id=state.pk if state else None,
            exclude_id=self.instance.pk if self.instance and self.instance.pk else None,
        )
        if not matches:
            return cleaned

        listed = format_html_join(
            "",
            "<li><b>{}</b> — {}, {} · {}% match</li>",
            (
                (
                    match.organisation.name,
                    match.organisation.district or _("district unknown"),
                    match.organisation.get_type_display(),
                    round(match.score * 100),
                )
                for match in matches
            ),
        )
        raise forms.ValidationError(
            format_html(
                "{}<ul>{}</ul>{}",
                _("This looks like an organisation that is already in the registry:"),
                listed,
                _(
                    "Open the match and edit it, or tick the confirmation box below "
                    "if these really are different bodies."
                ),
            )
        )


class ProfileInline(admin.StackedInline):
    """One-to-one extension; never more than one, and deleting it is fine."""

    extra = 0
    max_num = 1
    can_delete = True


class FpoProfileInline(ProfileInline):
    model = FpoProfile
    verbose_name_plural = _("FPO profile")
    fieldsets = (
        (
            _("Capital and membership"),
            {
                "fields": (
                    ("authorised_capital", "paid_up_capital"),
                    ("share_value_inr", "shareholder_count"),
                )
            },
        ),
        (
            _("Business"),
            {"fields": ("business_lines", "licences", "primary_crops")},
        ),
        (
            _("Infrastructure"),
            {
                "fields": (
                    ("has_storage", "storage_capacity_mt"),
                    ("has_processing_unit", "processing_details"),
                    "custom_hiring_centre",
                )
            },
        ),
        (
            _("Promotion and compliance"),
            {
                "fields": (
                    ("implementing_agency", "cbbo_name"),
                    ("equity_grant_received", "credit_guarantee"),
                    ("last_agm_date", "last_annual_return_fy"),
                )
            },
        ),
    )


class SugarMillProfileInline(ProfileInline):
    model = SugarMillProfile
    verbose_name_plural = _("sugar mill profile")
    fieldsets = (
        (
            _("Capacity"),
            {
                "fields": (
                    ("ownership", "installed_year", "is_operational"),
                    ("crushing_capacity_tcd", "avg_recovery_pct"),
                    ("cogeneration_mw", "refinery_capacity_tpd"),
                )
            },
        ),
        (
            _("Diversification"),
            {"fields": (("has_ethanol_plant", "distillery_capacity_klpd"), "has_cbg_plant")},
        ),
        (
            _("Cane command area"),
            {
                "fields": (
                    ("cane_command_villages", "registered_cane_growers"),
                    ("cane_price_srp_inr", "cane_payment_status", "cane_arrears_inr_cr"),
                )
            },
        ),
        (
            # 🔴 The BD calendar is built from these two columns: mill
            # decision-makers are unreachable during crushing and available
            # May-September.
            _("Season and affiliation"),
            {"fields": (("season_start_month", "season_end_month"), "federation_membership")},
        ),
    )


class CooperativeProfileInline(ProfileInline):
    model = CooperativeProfile
    fk_name = "organisation"
    verbose_name_plural = _("cooperative profile")
    autocomplete_fields = ("affiliated_to_org",)
    fields = (
        ("society_type", "is_pacs", "is_computerised"),
        ("registration_act", "affiliated_to_org"),
        ("deposit_base_inr", "loan_outstanding_inr"),
        ("area_of_operation", "villages_covered"),
    )


#: Which profile extension belongs to which discriminator value. Types absent
#: from this map have no extension — a bank or an input dealer is fully
#: described by the base row.
PROFILE_FOR_TYPE = {
    OrgType.FPO: FpoProfileInline,
    OrgType.SUGAR_MILL: SugarMillProfileInline,
    OrgType.ACS: CooperativeProfileInline,
    OrgType.COOPERATIVE_FEDERATION: CooperativeProfileInline,
}


class OrgAnnualMetricInline(admin.TabularInline):
    model = OrgAnnualMetric
    extra = 0
    fields = ("fy", "metric_code", "metric_value", "unit", "source")
    autocomplete_fields = ("source",)
    ordering = ("-fy", "metric_code")


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    form = OrganisationAdminForm

    list_display = (
        "name",
        "type",
        "status",
        "tier_badge",
        "district",
        "state",
        "member_count",
        "owner_user",
        "deleted_marker",
    )
    list_filter = ("type", "status", "quality_tier", "legal_form", "state", "is_deleted")
    search_fields = ("name", "name_local", "short_name", "org_code", "cin", "gstin")
    list_select_related = ("district", "state", "owner_user")
    autocomplete_fields = ("state", "district", "block", "village", "parent_org", "primary_source")
    ordering = ("name",)
    list_per_page = 50
    save_on_top = True
    actions = ("soft_delete", "restore")

    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by", "merged_into")

    fieldsets = (
        (
            None,
            {
                "fields": (
                    ("name", "name_local"),
                    ("short_name", "org_code"),
                    ("type", "status", "legal_form"),
                    "aliases",
                )
            },
        ),
        (
            _("Statutory identifiers"),
            {
                "description": _(
                    "Business data, not personal data. A full PAN never enters this form — "
                    "mask it as ABCDE****F."
                ),
                "fields": (
                    ("cin", "registration_no"),
                    ("registration_act", "registration_date"),
                    ("gstin", "pan_masked", "udyam_no"),
                ),
            },
        ),
        (
            _("Where it is"),
            {
                "fields": (
                    ("state", "district"),
                    ("block", "village"),
                    ("address_line1", "address_line2"),
                    ("pincode", "latitude", "longitude"),
                )
            },
        ),
        (
            _("Scale"),
            {
                "fields": (
                    ("established_year", "website"),
                    ("member_count", "women_member_count"),
                    ("annual_turnover_inr", "turnover_fy"),
                )
            },
        ),
        (
            _("Relationships"),
            {"fields": ("parent_org", "promoting_agency", "scheme_reference")},
        ),
        (
            _("Quality and ownership"),
            {
                "description": _(
                    "Tier drives whether this record may be used for campaigns and "
                    "client-facing counts. Bronze is a lead, not a fact."
                ),
                "fields": (
                    ("quality_tier", "completeness_score"),
                    ("primary_source", "owner_user"),
                    "tags",
                ),
            },
        ),
        (
            _("Record state"),
            {
                "classes": ("collapse",),
                "fields": (
                    ("is_deleted", "merged_into"),
                    ("created_at", "created_by"),
                    ("updated_at", "updated_by"),
                    "extra",
                ),
            },
        ),
        (None, {"fields": ("confirm_not_duplicate",)}),
    )

    #: Tombstones stay visible and filterable. `Organisation.live` is for
    #: user-facing reads; an admin who cannot see a deleted row cannot restore
    #: one.
    def get_queryset(self, request):
        return Organisation.objects.select_related("district", "state", "owner_user")

    def get_search_results(self, request, queryset, search_term):
        """
        Search the alias list too.

        `aliases` is a `text[]`, and the admin's default `icontains` has no
        meaning against an array, so it is flattened for the comparison.
        Aliases are where the other spellings live — "Chaudhary" for
        "Choudhary", the Devanagari form, the name on the MoU — and a registry
        you can only search by its canonical name is a registry analysts
        create duplicates in.
        """
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        if search_term:
            queryset |= (
                self.model.objects.annotate(
                    _aliases_text=Func(
                        F("aliases"),
                        Value(" | "),
                        function="array_to_string",
                        output_field=TextField(),
                    )
                )
                .filter(_aliases_text__icontains=search_term)
                .only("pk")
            )
            may_have_duplicates = True
        return queryset, may_have_duplicates

    def get_inlines(self, request, obj=None):
        inlines = [OrgAnnualMetricInline]
        profile = PROFILE_FOR_TYPE.get(obj.type) if obj else None
        if profile is not None:
            inlines.insert(0, profile)
        return inlines

    def has_delete_permission(self, request, obj=None) -> bool:
        """🔴 Nothing is hard-deleted. Use the soft-delete action."""
        return False

    def save_model(self, request, obj, form, change) -> None:
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    # -- Display helpers ---------------------------------------------------

    @admin.display(description=_("tier"), ordering="quality_tier")
    def tier_badge(self, obj: Organisation):
        colours = {
            "gold": "#7E5E00",
            "silver": "#4A5464",
            "bronze": "#8E4420",
            "quarantine": "#85292E",
        }
        return format_html(
            '<span style="color:{};font-weight:600">{}</span>',
            colours.get(obj.quality_tier, "#5C574B"),
            obj.get_quality_tier_display(),
        )

    @admin.display(description="", ordering="is_deleted")
    def deleted_marker(self, obj: Organisation):
        if obj.merged_into_id:
            return format_html('<span title="merged away">merged</span>')
        if obj.is_deleted:
            return format_html('<span title="soft-deleted">deleted</span>')
        return ""

    # -- Actions -----------------------------------------------------------

    @admin.action(description=_("Mark selected organisations as deleted"))
    def soft_delete(self, request, queryset) -> None:
        updated = queryset.filter(is_deleted=False).update(
            is_deleted=True, status=OrgStatus.DEFUNCT, updated_by=request.user
        )
        self.message_user(
            request,
            ngettext(
                "%(count)d organisation marked deleted. It is hidden from search and "
                "campaigns but still restorable.",
                "%(count)d organisations marked deleted. They are hidden from search and "
                "campaigns but still restorable.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @admin.action(description=_("Restore selected organisations"))
    def restore(self, request, queryset) -> None:
        updated = queryset.filter(is_deleted=True).update(is_deleted=False, updated_by=request.user)
        self.message_user(
            request,
            _("%(count)d restored. Check the status field — it was set to defunct on delete.")
            % {"count": updated},
            messages.SUCCESS,
        )
