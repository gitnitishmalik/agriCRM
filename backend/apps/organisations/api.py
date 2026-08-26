"""
Organisation endpoints. Doc 11 §3.

Two behaviours here are contract, not implementation detail:

* **Create returns 409 with candidates when it looks like a duplicate**, and
  `?force=true` overrides. The client is expected to show the candidates and
  make the user choose — the same decision the admin form forces, through a
  different door. Both go through `dedupe`, so they cannot drift apart.
* **Delete is a soft delete.** `DELETE /organisations/{id}/` sets
  `is_deleted`; it never removes a row. Merges, provenance and the audit trail
  all point at ids that must continue to resolve.

Filtering is explicit rather than generic. Doc 11 §1 describes a `__gte` /
`__in` / `__icontains` suffix language across every field; that arrives with
the shared filter backend, and until then this module supports the named
parameters below and nothing else, so that an unsupported filter fails loudly
instead of being silently ignored and quietly widening someone's export.
"""

from __future__ import annotations

from django.db.models import F, Func, Q, TextField, Value
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_field,
    extend_schema_view,
)
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.geography.api import DistrictSerializer, StateSerializer

from .dedupe import BLOCK_THRESHOLD, find_duplicates
from .models import (
    CooperativeProfile,
    FpoProfile,
    Organisation,
    OrgStatus,
    SugarMillProfile,
)


class FpoProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = FpoProfile
        exclude = ("organisation",)


class SugarMillProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = SugarMillProfile
        exclude = ("organisation",)


class CooperativeProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CooperativeProfile
        exclude = ("organisation",)


class OrganisationListSerializer(serializers.ModelSerializer):
    """
    The grid row. Deliberately narrow.

    A list of 50 organisations that each carry their full profile is four
    joins and roughly eight times the payload, to render a table showing six
    columns. Detail is a click away.
    """

    district_name = serializers.CharField(source="district.name", read_only=True, default=None)
    state_name = serializers.CharField(source="state.name", read_only=True, default=None)

    class Meta:
        model = Organisation
        fields = (
            "id",
            "org_code",
            "name",
            "name_local",
            "type",
            "status",
            "quality_tier",
            "completeness_score",
            "state",
            "state_name",
            "district",
            "district_name",
            "member_count",
            "updated_at",
        )


class OrganisationDetailSerializer(serializers.ModelSerializer):
    """The full record, including whichever profile the type discriminator implies."""

    state_detail = StateSerializer(source="state", read_only=True)
    district_detail = DistrictSerializer(source="district", read_only=True)
    profile = serializers.SerializerMethodField()

    class Meta:
        model = Organisation
        exclude = ("created_by", "updated_by")
        read_only_fields = ("id", "created_at", "updated_at", "completeness_score")

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_profile(self, obj: Organisation) -> dict | None:
        for attribute, serializer in (
            ("fpo_profile", FpoProfileSerializer),
            ("sugar_mill_profile", SugarMillProfileSerializer),
            ("cooperative_profile", CooperativeProfileSerializer),
        ):
            profile = getattr(obj, attribute, None)
            if profile is not None:
                return serializer(profile).data
        return None


class DuplicateCandidateSerializer(serializers.Serializer):
    """Doc 11 §3's duplicate response shape."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    district = serializers.CharField(allow_null=True)
    similarity = serializers.FloatField()
    matched_on = serializers.ListField(child=serializers.CharField())


def _as_candidate_payload(matches, *, scoped_to_district: bool) -> list[dict]:
    matched_on = ["name", "district"] if scoped_to_district else ["name", "state"]
    return [
        {
            "id": match.organisation.pk,
            "name": match.organisation.name,
            "district": match.organisation.district.name if match.organisation.district else None,
            "similarity": round(match.score, 3),
            "matched_on": matched_on,
        }
        for match in matches
    ]


class DuplicateCheckSerializer(serializers.Serializer):
    name = serializers.CharField()
    district = serializers.IntegerField(required=False, allow_null=True)
    state = serializers.IntegerField(required=False, allow_null=True)
    exclude_id = serializers.UUIDField(required=False, allow_null=True)


class BulkAssignSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=500)
    owner_user_id = serializers.UUIDField(allow_null=True)


@extend_schema_view(
    list=extend_schema(
        summary="List organisations",
        description=(
            "Soft-deleted rows are excluded unless `include_deleted=true`. An unsupported "
            "filter is a 400 rather than a silently ignored parameter."
        ),
        parameters=[
            OpenApiParameter("type", str, description="core.org_type value."),
            OpenApiParameter("status", str, description="core.org_status value."),
            OpenApiParameter(
                "quality_tier", str, description="gold | silver | bronze | quarantine"
            ),
            OpenApiParameter("state", int),
            OpenApiParameter("district", int),
            OpenApiParameter("owner", str, description="Owner's user public_id."),
            OpenApiParameter("member_count__gte", int),
            OpenApiParameter("member_count__lte", int),
            OpenApiParameter("q", str, description="Name, local name, alias, org code, CIN."),
            OpenApiParameter(
                "include_deleted",
                bool,
                description="Include soft-deleted rows. Off by default; nothing is hard-deleted.",
            ),
        ],
    ),
    create=extend_schema(
        summary="Create an organisation",
        description=(
            "Returns **409** with candidate matches when the name looks like an "
            "organisation already in the registry for that district. Pass `?force=true` "
            "to create anyway — the override is recorded on the row."
        ),
        parameters=[OpenApiParameter("force", bool, description="Override duplicate blocking.")],
    ),
    destroy=extend_schema(
        summary="Soft delete an organisation",
        description="Sets `is_deleted`; the row and its id continue to resolve.",
    ),
    retrieve=extend_schema(
        summary="Fetch one organisation",
        description=(
            "Includes the profile implied by the type discriminator. Resolves "
            "soft-deleted rows too, so a stored id never turns into a 404 that "
            "looks like the id was wrong."
        ),
    ),
    update=extend_schema(summary="Replace an organisation"),
    partial_update=extend_schema(summary="Update part of an organisation"),
)
class OrganisationViewSet(viewsets.ModelViewSet):
    serializer_class = OrganisationDetailSerializer

    #: Named filters this endpoint honours. Anything else in the query string
    #: that is not plumbing is rejected, so a typo cannot silently widen a
    #: result set someone is about to export.
    FILTER_PARAMS = frozenset(
        {
            "type",
            "status",
            "quality_tier",
            "state",
            "district",
            "owner",
            "member_count__gte",
            "member_count__lte",
            "q",
            "include_deleted",
        }
    )
    PLUMBING_PARAMS = frozenset({"limit", "cursor", "ordering", "format", "force", "fields"})

    def get_serializer_class(self):
        if self.action == "list":
            return OrganisationListSerializer
        return OrganisationDetailSerializer

    def get_queryset(self):
        params = self.request.query_params

        unknown = set(params) - self.FILTER_PARAMS - self.PLUMBING_PARAMS
        if unknown:
            raise ValidationError(
                {"detail": f"Unsupported filter(s): {', '.join(sorted(unknown))}."}
            )

        include_deleted = params.get("include_deleted") in ("1", "true", "True")
        manager = Organisation.objects if include_deleted else Organisation.live
        queryset = manager.select_related("state", "district").order_by("-created_at")

        for param, field in (
            ("type", "type"),
            ("status", "status"),
            ("quality_tier", "quality_tier"),
            ("state", "state_id"),
            ("district", "district_id"),
            ("owner", "owner_user__public_id"),
            ("member_count__gte", "member_count__gte"),
            ("member_count__lte", "member_count__lte"),
        ):
            if value := params.get(param):
                queryset = queryset.filter(**{field: value})

        if term := params.get("q", "").strip():
            queryset = queryset.annotate(
                _aliases_text=Func(
                    F("aliases"),
                    Value(" | "),
                    function="array_to_string",
                    output_field=TextField(),
                )
            ).filter(
                Q(name__icontains=term)
                | Q(name_local__icontains=term)
                | Q(_aliases_text__icontains=term)
                | Q(org_code__iexact=term)
                | Q(cin__iexact=term)
            )

        return queryset

    def get_object(self):
        # Detail routes see tombstones. A client following a stored id after a
        # soft delete should get the record and its state, not a 404 that
        # looks like the id was wrong.
        queryset = Organisation.objects.select_related("state", "district")
        obj = queryset.filter(pk=self.kwargs["pk"]).first()
        if obj is None:
            raise Http404
        self.check_object_permissions(self.request, obj)
        return obj

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        forced = request.query_params.get("force") in ("1", "true", "True")
        if not forced:
            district = serializer.validated_data.get("district")
            state = serializer.validated_data.get("state")
            matches = find_duplicates(
                serializer.validated_data["name"],
                district_id=district.pk if district else None,
                state_id=state.pk if state else None,
            )
            if matches:
                return Response(
                    {
                        "error": {
                            "code": "conflict",
                            "message": f"{len(matches)} similar organisations found",
                            "details": {
                                "candidates": _as_candidate_payload(
                                    matches, scoped_to_district=district is not None
                                ),
                                "threshold": BLOCK_THRESHOLD,
                            },
                        }
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def perform_create(self, serializer) -> None:
        extra = {"created_by": self.request.user, "updated_by": self.request.user}
        if self.request.query_params.get("force") in ("1", "true", "True"):
            # Recorded, not just permitted. When a duplicate does turn up in
            # the merge queue six months from now, the question is always
            # "who decided these were different, and when".
            extra["extra"] = {
                **(serializer.validated_data.get("extra") or {}),
                "duplicate_override": {
                    "by": str(self.request.user.public_id),
                    "at": timezone.now().isoformat(),
                },
            }
        serializer.save(**extra)

    def perform_update(self, serializer) -> None:
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance) -> None:
        """🔴 Soft delete only. Doc 02 §6: nothing in this system is removed."""
        instance.is_deleted = True
        instance.status = OrgStatus.DEFUNCT
        instance.updated_by = self.request.user
        instance.save(update_fields=["is_deleted", "status", "updated_by", "updated_at"])

    @extend_schema(
        summary="Check a name for likely duplicates",
        request=DuplicateCheckSerializer,
        responses={200: DuplicateCandidateSerializer(many=True)},
        description=(
            "Live duplicate check for a create form. Same scorer the create endpoint "
            "and the admin use, so a client can show the warning before the user has "
            "filled in the rest of the record."
        ),
    )
    @action(detail=False, methods=["post"], url_path="check-duplicates")
    def check_duplicates(self, request):
        payload = DuplicateCheckSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        matches = find_duplicates(
            data["name"],
            district_id=data.get("district"),
            state_id=data.get("state"),
            exclude_id=data.get("exclude_id"),
        )
        return Response(
            {
                "candidates": _as_candidate_payload(
                    matches, scoped_to_district=data.get("district") is not None
                ),
                "threshold": BLOCK_THRESHOLD,
            }
        )

    @extend_schema(
        summary="Reassign the owner of many organisations",
        request=BulkAssignSerializer,
        responses={200: None},
    )
    @action(detail=False, methods=["post"], url_path="bulk-assign")
    def bulk_assign(self, request):
        payload = BulkAssignSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        updated = Organisation.live.filter(pk__in=payload.validated_data["ids"]).update(
            owner_user_id=payload.validated_data["owner_user_id"], updated_by=request.user
        )
        return Response({"updated": updated})
