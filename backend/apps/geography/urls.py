"""Reference lookups. Read-only; LGD sync is what writes here."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from .api import BlockViewSet, CropViewSet, DistrictViewSet, StateViewSet, VillageViewSet

router = DefaultRouter()
router.register("states", StateViewSet, basename="state")
router.register("districts", DistrictViewSet, basename="district")
router.register("blocks", BlockViewSet, basename="block")
router.register("villages", VillageViewSet, basename="village")
router.register("crops", CropViewSet, basename="crop")

urlpatterns = router.urls
