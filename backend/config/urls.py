"""
Root URL configuration.

Doc 11 §13: URL-versioned at /api/v1/. Breaking changes get /api/v2/ with v1
supported for 12 months — the mobile app is the constraint, because field
agents update slowly.
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

API = "api/v1/"

urlpatterns = [
    # Django Admin is the Phase 1 data-ops console, not an afterthought
    # (Doc 03 §11). It is a first-class surface for this project.
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(f"{API}auth/", include("apps.accounts.urls")),
    path(f"{API}", include("apps.reporting.urls")),  # /healthz lives here
]
