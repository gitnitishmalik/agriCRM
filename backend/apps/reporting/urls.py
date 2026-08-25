"""Health and readiness probes. Dashboards arrive in Phase 3."""

from django.urls import path

from apps.accounts.views import HealthView

urlpatterns = [
    path("healthz/", HealthView.as_view(), name="healthz"),
]
