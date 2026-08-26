"""
Geography lookup endpoints.

Read-only on purpose. Reference data changes when LGD publishes a change, and
it arrives through the `lgd_sync` collector so that the LGD code, the effective
date and the provenance row all land together. A district created through a
POST would have none of those, and every subsequent sync would either duplicate
it or fight it.

These are the endpoints the create forms cascade against — pick a state, get
its districts, pick one, get its blocks — so they are shaped for that: filter
by parent, search by name, no nesting.
"""

from __future__ import annotations

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import serializers, viewsets
from rest_framework.exceptions import ValidationError

from config.pagination import ReferenceCursorPagination

from .models import Block, Crop, District, State, Village


class StateSerializer(serializers.ModelSerializer):
    class Meta:
        model = State
        fields = ("id", "lgd_code", "name", "name_local", "iso_code", "is_ut")


class DistrictSerializer(serializers.ModelSerializer):
    state_name = serializers.CharField(source="state.name", read_only=True)

    class Meta:
        model = District
        fields = ("id", "lgd_code", "name", "name_local", "state", "state_name")


class BlockSerializer(serializers.ModelSerializer):
    district_name = serializers.CharField(source="district.name", read_only=True)

    class Meta:
        model = Block
        fields = ("id", "lgd_code", "name", "name_local", "district", "district_name")


class VillageSerializer(serializers.ModelSerializer):
    district_name = serializers.CharField(source="district.name", read_only=True)

    class Meta:
        model = Village
        fields = (
            "id",
            "lgd_code",
            "name",
            "name_local",
            "block",
            "district",
            "district_name",
            "pincode",
            "latitude",
            "longitude",
        )


class CropSerializer(serializers.ModelSerializer):
    class Meta:
        model = Crop
        fields = ("id", "code", "name", "name_local", "category", "default_season")


class ReferenceViewSet(viewsets.ReadOnlyModelViewSet):
    pagination_class = ReferenceCursorPagination
    throttle_scope = "sync"

    #: `?q=` matches against these, case-insensitively.
    search_fields: tuple[str, ...] = ("name", "name_local")

    def filter_by_query(self, queryset):
        term = self.request.query_params.get("q", "").strip()
        if not term:
            return queryset

        condition = Q()
        for field in self.search_fields:
            condition |= Q(**{f"{field}__icontains": term})
        return queryset.filter(condition)


@extend_schema_view(
    list=extend_schema(
        summary="List states and union territories",
        parameters=[OpenApiParameter("q", str, description="Match against name or name_local.")],
    ),
    retrieve=extend_schema(summary="Fetch one state by id"),
)
class StateViewSet(ReferenceViewSet):
    queryset = State.objects.all().order_by("id")
    serializer_class = StateSerializer

    def get_queryset(self):
        return self.filter_by_query(super().get_queryset())


@extend_schema_view(
    list=extend_schema(
        summary="List districts, optionally within one state",
        parameters=[
            OpenApiParameter("state", int, description="Filter to one state id."),
            OpenApiParameter("q", str, description="Match against name or name_local."),
        ],
    ),
    retrieve=extend_schema(summary="Fetch one district by id"),
)
class DistrictViewSet(ReferenceViewSet):
    queryset = District.objects.select_related("state").order_by("id")
    serializer_class = DistrictSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        state = self.request.query_params.get("state")
        if state:
            queryset = queryset.filter(state_id=state)
        return self.filter_by_query(queryset)


@extend_schema_view(
    list=extend_schema(
        summary="List blocks / tehsils, optionally within one district",
        parameters=[
            OpenApiParameter("district", int, description="Filter to one district id."),
            OpenApiParameter("q", str, description="Match against name or name_local."),
        ],
    ),
    retrieve=extend_schema(summary="Fetch one block by id"),
)
class BlockViewSet(ReferenceViewSet):
    queryset = Block.objects.select_related("district").order_by("id")
    serializer_class = BlockSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        district = self.request.query_params.get("district")
        if district:
            queryset = queryset.filter(district_id=district)
        return self.filter_by_query(queryset)


@extend_schema_view(
    list=extend_schema(
        summary="List villages within a district, block or pincode",
        description=(
            "🔴 Requires `district`, `block` or `pincode`. `ref.village` holds roughly "
            "660,000 rows; an unscoped list of it is a sequential scan answering a "
            "question nobody asked."
        ),
        parameters=[
            OpenApiParameter("district", int, description="District id."),
            OpenApiParameter("block", int, description="Block id."),
            OpenApiParameter("pincode", str, description="Six-digit pincode."),
            OpenApiParameter("q", str, description="Match against name or name_local."),
        ],
    ),
    retrieve=extend_schema(summary="Fetch one village by id"),
)
class VillageViewSet(ReferenceViewSet):
    queryset = Village.objects.select_related("district", "block").order_by("id")
    serializer_class = VillageSerializer
    search_fields = ("name", "name_local")

    #: One of these must be supplied to list villages. Not a nicety — the
    #: table is large enough that an unscoped query is a production incident.
    REQUIRED_SCOPES = ("district", "block", "pincode")

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        if self.action == "list" and not any(params.get(s) for s in self.REQUIRED_SCOPES):
            raise ValidationError(
                {
                    "detail": "Listing villages requires one of: "
                    + ", ".join(self.REQUIRED_SCOPES)
                    + "."
                }
            )

        if district := params.get("district"):
            queryset = queryset.filter(district_id=district)
        if block := params.get("block"):
            queryset = queryset.filter(block_id=block)
        if pincode := params.get("pincode"):
            queryset = queryset.filter(pincode=pincode)
        return self.filter_by_query(queryset)


@extend_schema_view(
    list=extend_schema(
        summary="List crops",
        parameters=[OpenApiParameter("q", str, description="Match against name or code.")],
    ),
    retrieve=extend_schema(summary="Fetch one crop by id"),
)
class CropViewSet(ReferenceViewSet):
    queryset = Crop.objects.all().order_by("id")
    serializer_class = CropSerializer
    search_fields = ("name", "name_local", "code")

    def get_queryset(self):
        return self.filter_by_query(super().get_queryset())
