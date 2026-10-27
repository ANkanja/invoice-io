from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "phone", "email", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "phone", "email")

    def has_delete_permission(self, request, obj=None):
        return False
