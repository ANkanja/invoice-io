from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import Payment, PaymentAllocation, PaymentMethod


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("name", "requires_reference", "is_active", "sort_order")
    list_editable = ("requires_reference", "is_active", "sort_order")

    def has_delete_permission(self, request, obj=None):
        return False


class AllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("payment_date", "payer_name", "amount", "method", "reference", "is_reversed")
    list_filter = ("method", "is_reversed")
    search_fields = ("payer_name", "reference")
    inlines = [AllocationInline]
