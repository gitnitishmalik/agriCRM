"""Organisation registry routes. Doc 11 §3."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from .api import OrganisationViewSet

router = DefaultRouter()
router.register("organisations", OrganisationViewSet, basename="organisation")

urlpatterns = router.urls
