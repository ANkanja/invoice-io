from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import Invoice, InvoiceLine


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(ReadOnlyAdmin):
    list_display = ("number", "status", "customer_name", "issue_date", "total")
    list_filter = ("status",)
    search_fields = ("number", "customer_name")
    inlines = [InvoiceLineInline]
