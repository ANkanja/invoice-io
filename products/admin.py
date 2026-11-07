from django import forms
from django.contrib import admin

from .models import Category, Product, SellingOption, Unit


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    search_fields = ("name",)


class UnitAdminForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["code", "name", "plural", "symbol", "dimension", "is_active"]

    def clean(self):
        data = super().clean()
        # Volume and mass units are fixed (ml, L, g, kg) so conversions stay exact.
        # New units are count units only, e.g. sachet, jerrycan, drum.
        if self.instance.pk is None and data.get("dimension") != Unit.Dimension.COUNT:
            raise forms.ValidationError("Only count units (sachet, jerrycan…) can be added. ml, L, g and kg are built in.")
        if self.instance.pk and data.get("dimension") != self.instance.dimension:
            raise forms.ValidationError("A unit's dimension can't be changed.")
        return data


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    form = UnitAdminForm
    list_display = ("name", "symbol", "dimension", "factor", "is_active")
    list_filter = ("dimension",)

    def has_delete_permission(self, request, obj=None):
        return False


class SellingOptionInline(admin.TabularInline):
    model = SellingOption
    extra = 0
    fields = ("label", "method", "package_unit", "content_quantity", "price_unit", "price", "institution_price", "cost_price", "is_default", "is_active")
    can_delete = False


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "stock_unit", "track_stock", "stock_qty", "is_active")
    list_filter = ("is_active", "track_stock", "category")
    search_fields = ("name", "sku")
    readonly_fields = ("stock_qty", "legacy_unit", "legacy_package_size", "legacy_price", "legacy_cost_price",
                       "created_at", "updated_at")
    inlines = [SellingOptionInline]

    def has_delete_permission(self, request, obj=None):
        # Archive instead (untick "is active"); used products are protected anyway.
        return False
