"""
Source register admin.

🔴 This screen is where R1 and R4 are actually enforced against a human rather
than against code. A collector reads `is_approved` and refuses to run; the
only place that flag gets set is here, by a named person, and the form will
not let them set it without a written legal basis or let a source claim to
carry personal data through a route Doc 05 does not permit.

Approval is stamped, not typed: `approved_by` and `approved_at` are filled
from the request and the clock. Someone typing a colleague's name into an
approval field is exactly the audit trail you do not want.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import PII_PERMITTED_SOURCE_KINDS, Contradiction, FieldProvenance, Source


class SourceAdminForm(forms.ModelForm):
    class Meta:
        model = Source
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        contains_pii = cleaned.get("contains_pii")
        legal_basis = (cleaned.get("legal_basis") or "").strip()
        is_approved = cleaned.get("is_approved")

        # 🔴 R4 — personal data enters only via partner agreement, field
        # collection, inbound signup, or an approved Theta / licensed batch.
        if contains_pii and kind not in PII_PERMITTED_SOURCE_KINDS:
            permitted = ", ".join(sorted(k.label for k in PII_PERMITTED_SOURCE_KINDS))
            self.add_error(
                "contains_pii",
                _(
                    "R4: personal data may not enter through a %(kind)s source. "
                    "Permitted routes are: %(permitted)s. If this source really does "
                    "carry personal data, its kind is wrong — or it should not be here."
                )
                % {"kind": kind, "permitted": permitted},
            )

        # 🔴 R1's precondition. An approval nobody can justify in writing is
        # not an approval; it is a note saying someone was in a hurry.
        if is_approved and not legal_basis:
            self.add_error(
                "legal_basis",
                _("A source cannot be approved without a written legal basis."),
            )

        return cleaned


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    form = SourceAdminForm

    list_display = ("code", "name", "kind", "approval", "contains_pii", "refresh_cadence")
    list_filter = ("is_approved", "contains_pii", "kind")
    search_fields = ("code", "name", "url", "legal_basis")
    ordering = ("code",)
    readonly_fields = ("approved_by", "approved_at", "created_at")

    fieldsets = (
        (None, {"fields": (("code", "name"), ("kind", "url"), "refresh_cadence")}),
        (
            _("Legal position"),
            {
                "description": _(
                    "🔴 Collectors call source.is_approved before their first request and "
                    "exit non-zero if it is false. Approving a source here is what lets "
                    "data from it into the database."
                ),
                "fields": ("legal_basis", "licence", "contains_pii", "is_approved"),
            },
        ),
        (
            _("Approval record"),
            {"fields": (("approved_by", "approved_at"), "created_at", "notes")},
        ),
    )

    def has_delete_permission(self, request, obj=None) -> bool:
        """A source is referenced by every row it produced. Deleting it orphans provenance."""
        return False

    def save_model(self, request, obj, form, change) -> None:
        was_approved = (
            Source.objects.filter(pk=obj.pk).values_list("is_approved", flat=True).first()
            if change
            else False
        )
        if obj.is_approved and not was_approved:
            obj.approved_by = f"{request.user.full_name} <{request.user.email}>"
            obj.approved_at = timezone.now()
        elif not obj.is_approved:
            # Withdrawing approval clears the stamp. Leaving a stale name next
            # to a revoked source reads as though they still stand behind it.
            obj.approved_by = None
            obj.approved_at = None
        super().save_model(request, obj, form, change)

    @admin.display(description=_("approval"), ordering="is_approved", boolean=False)
    def approval(self, obj: Source):
        if not obj.is_approved:
            return format_html('<span style="color:#85292E">not approved</span>')
        return format_html(
            '<span style="color:#7E5E00" title="{}">approved</span>', obj.approved_by or ""
        )


@admin.register(FieldProvenance)
class FieldProvenanceAdmin(admin.ModelAdmin):
    """
    Read-only. Provenance is written by the ingestion pipeline, and a row
    edited by hand is a row that no longer records what actually happened.
    """

    list_display = (
        "entity_type",
        "entity_id",
        "field_name",
        "value_text",
        "source",
        "confidence",
        "collected_at",
        "is_current",
    )
    list_filter = ("entity_type", "is_current", "source")
    search_fields = ("entity_id", "field_name", "value_text")
    list_select_related = ("source",)
    date_hierarchy = "collected_at"
    ordering = ("-collected_at",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Contradiction)
class ContradictionAdmin(admin.ModelAdmin):
    """
    The analyst queue. Two sources disagree; a person decides.

    Only `resolution` is editable — the two values and their provenance are
    the evidence, and evidence that can be edited during review is not
    evidence. An unresolved contradiction older than 30 days moves the entity
    to quarantine (Doc 07 §2), which is why this list defaults to open ones.
    """

    list_display = ("entity_type", "entity_id", "field_name", "value_a", "value_b", "detected_at")
    list_filter = ("entity_type", ("resolved_at", admin.EmptyFieldListFilter))
    search_fields = ("entity_id", "field_name")
    date_hierarchy = "detected_at"
    ordering = ("-detected_at",)
    readonly_fields = (
        "entity_type",
        "entity_id",
        "field_name",
        "value_a",
        "value_b",
        "provenance_a",
        "provenance_b",
        "detected_at",
        "resolved_at",
        "resolved_by",
    )
    fields = (*readonly_fields, "resolution")

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def save_model(self, request, obj, form, change) -> None:
        if obj.resolution and obj.resolved_at is None:
            obj.resolved_at = timezone.now()
            obj.resolved_by = request.user.public_id
        super().save_model(request, obj, form, change)
