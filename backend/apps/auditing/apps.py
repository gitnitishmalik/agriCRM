from django.apps import AppConfig


class AuditingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.auditing"
    verbose_name = "Change log, access log, DSR handling"
    """Change log, access log, DSR handling."""
