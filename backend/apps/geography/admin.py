"""
Geography admin.

Reference data behaves differently from master data: it is loaded by the LGD
sync, not typed in, and it is read far more often than it is written. So these
screens are tuned for *looking things up* — trigram-backed name search, a
visible LGD code on every row — and adding is left available but unadvertised.

🔴 `ref.village` reaches roughly 660,000 rows. An unfiltered changelist over
that is a full scan and a count, on every page load, for a list nobody can
read. `VillageAdmin` therefore shows nothing until you narrow it, which is a
deliberate refusal rather than an empty state.
"""

from __future__ import annotations

from django.contrib import admin
from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from .models import Block, Crop, CropVariety, District, State, Village


class ReferenceAdmin(admin.ModelAdmin):
    """
    Shared posture for `ref` tables.

    Deletion is off. These rows are referenced by organisations, farmers and
    territory rules; the fix for a wrong district is a correction or a merge,
    never a delete that orphans everything pointing at it.
    """

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(State)
class StateAdmin(ReferenceAdmin):
    list_display = ("name", "lgd_code", "iso_code", "is_ut", "district_count")
    list_filter = ("is_ut",)
    search_fields = ("name", "name_local", "iso_code")
    ordering = ("name",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_district_count=Count("districts"))

    @admin.display(description=_("districts"), ordering="_district_count")
    def district_count(self, obj: State) -> int:
        return obj._district_count


@admin.register(District)
class DistrictAdmin(ReferenceAdmin):
    list_display = ("name", "state", "lgd_code", "name_local")
    list_filter = ("state",)
    search_fields = ("name", "name_local", "lgd_code")
    list_select_related = ("state",)
    ordering = ("state__name", "name")


@admin.register(Block)
class BlockAdmin(ReferenceAdmin):
    list_display = ("name", "district", "state_name", "lgd_code")
    list_filter = ("district__state",)
    search_fields = ("name", "name_local", "lgd_code")
    list_select_related = ("district", "district__state")
    autocomplete_fields = ("district",)
    ordering = ("district__name", "name")

    @admin.display(description=_("state"), ordering="district__state__name")
    def state_name(self, obj: Block) -> str:
        return obj.district.state.name


@admin.register(Village)
class VillageAdmin(ReferenceAdmin):
    list_display = ("name", "block", "district", "lgd_code", "pincode")
    list_filter = ("district__state",)
    search_fields = ("name", "name_local", "lgd_code", "pincode")
    list_select_related = ("block", "district")
    autocomplete_fields = ("block", "district")
    ordering = ("name",)

    # Counting 660k rows to render "1 of 660,124" costs more than the page it
    # decorates, and nobody pages to the end of it.
    show_full_result_count = False
    list_per_page = 50

    #: Changelist plumbing — paging, sort order, popup state. None of these
    #: narrows the result set, so none of them lifts the refusal below.
    NON_NARROWING_PARAMS = frozenset(
        {"p", "o", "ot", "e", "_changelist_filters", "_to_field", "_popup"}
    )

    def get_queryset(self, request):
        """
        Return nothing until the list is narrowed.

        A village list is only useful once you know which district you are
        looking in. Showing the first fifty of six hundred thousand in
        alphabetical order is not a useful default — it is a slow one that
        looks like an answer.
        """
        queryset = super().get_queryset(request)
        if not any(
            value for key, value in request.GET.items() if key not in self.NON_NARROWING_PARAMS
        ):
            return queryset.none()
        return queryset


class CropVarietyInline(admin.TabularInline):
    model = CropVariety
    extra = 0
    fields = ("name", "maturity_days")


@admin.register(Crop)
class CropAdmin(ReferenceAdmin):
    list_display = ("name", "code", "category", "default_season", "variety_count")
    list_filter = ("category", "default_season")
    search_fields = ("name", "code", "name_local")
    inlines = (CropVarietyInline,)
    ordering = ("name",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_variety_count=Count("varieties"))

    @admin.display(description=_("varieties"), ordering="_variety_count")
    def variety_count(self, obj: Crop) -> int:
        return obj._variety_count
