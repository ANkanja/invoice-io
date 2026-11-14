from django.contrib import admin

from .models import RestockLine, RestockPlan, RestockPurchase


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LineInline(admin.TabularInline):
    model = RestockLine
    extra = 0
    can_delete = False
    fields = ("description", "unit_label", "suggested_qty", "planned_qty", "planned_unit_cost")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(RestockPlan)
class RestockPlanAdmin(ReadOnlyAdmin):
    list_display = ("month", "budget", "created_by")
    inlines = [LineInline]


@admin.register(RestockPurchase)
class RestockPurchaseAdmin(ReadOnlyAdmin):
    list_display = ("line", "quantity", "amount_paid", "purchase_date", "is_reversed", "created_by")
    list_filter = ("is_reversed",)
