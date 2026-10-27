from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import StockMovement


@admin.register(StockMovement)
class StockMovementAdmin(ReadOnlyAdmin):
    """Stock is changed only through the app's stock screens (Phase 2) and
    invoice issuing, so the ledger is view-only here."""

    list_display = ("created_at", "product", "kind", "quantity", "balance_after", "reference", "created_by")
    list_filter = ("kind",)
    search_fields = ("product__name", "reference")
