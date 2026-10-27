from django.contrib import admin

from .models import AuditLog, BusinessSettings


class ReadOnlyAdmin(admin.ModelAdmin):
    """For ledgers and financial history: viewable, never editable here."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BusinessSettings)
class BusinessSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not BusinessSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "actor", "action", "object_repr", "reason")
    list_filter = ("action",)
    search_fields = ("object_repr", "reason")
