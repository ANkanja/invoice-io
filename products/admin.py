from django.contrib import admin

from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    search_fields = ("name",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "package_size", "category", "default_price", "track_stock", "stock_qty", "is_active")
    list_filter = ("is_active", "track_stock", "category")
    search_fields = ("name", "sku")
    readonly_fields = ("stock_qty", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        # Archive instead (untick "is active"); used products are protected anyway.
        return False
