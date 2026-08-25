from django.apps import AppConfig


class DataqualityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dataquality"
    verbose_name = "Sources, provenance, scoring, dedupe, imports, merges"
    """Sources, provenance, scoring, dedupe, imports, merges.

    🔴 Doc 03 §10: this app requires >=80% test coverage. It is one of
    the two places where a bug becomes a legal problem rather than a
    defect. Treat every change here as having legal consequences until
    proven otherwise."""
