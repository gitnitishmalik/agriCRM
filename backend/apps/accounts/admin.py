from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "full_name", "role", "is_active", "mfa_enforced")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("email", "full_name")
    # mfa_enforced is derived in save() — showing it editable would imply
    # an admin can turn MFA off for a role that requires it.
    readonly_fields = ("mfa_enforced", "last_login", "date_joined")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Identity", {"fields": ("full_name", "phone_e164")}),
        ("Access", {"fields": ("role", "district_ids", "mfa_enforced")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Dates", {"fields": ("last_login", "date_joined", "deactivated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name", "role", "password1", "password2"),
            },
        ),
    )
