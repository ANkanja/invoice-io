from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import AppendOnlyModel


class StockMovement(AppendOnlyModel):
    """Append-only stock ledger. Product.stock_qty always equals the sum of
    `quantity` over a product's movements (see `manage.py check_stock`).

    Sign convention: positive = stock in, negative = stock out.
    Mistakes are corrected with a new ADJUSTMENT, never by editing a row.
    """

    class Kind(models.TextChoices):
        STOCK_IN = "stock_in", "Stock in"            # deliveries, production, opening stock
        STOCK_OUT = "stock_out", "Stock out"         # damaged, expired, own use, samples
        ADJUSTMENT = "adjustment", "Count adjustment"  # physical count correction (±)
        SALE = "sale", "Sale (invoice issued)"
        SALE_REVERSAL = "sale_reversal", "Sale reversed (invoice cancelled)"

    class OutReason(models.TextChoices):
        DAMAGED = "damaged", "Damaged"
        EXPIRED = "expired", "Expired"
        OWN_USE = "own_use", "Own / internal use"
        SAMPLE = "sample", "Free sample / promotion"
        LOST = "lost", "Lost or stolen"
        OTHER = "other", "Other"

    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="stock_movements")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    out_reason = models.CharField(max_length=20, choices=OutReason.choices, blank=True)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reference = models.CharField(max_length=80, blank=True, help_text="Supplier invoice, batch number, etc.")
    notes = models.TextField(blank=True)

    invoice_line = models.ForeignKey(
        "invoices.InvoiceLine", on_delete=models.PROTECT, null=True, blank=True, related_name="stock_movements"
    )
    reverses = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversed_by"
    )

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["product", "created_at"])]
        constraints = [
            models.CheckConstraint(condition=~Q(quantity=0), name="stock_movement_quantity_nonzero"),
            # Direction must match the kind.
            models.CheckConstraint(
                condition=~Q(kind__in=["stock_in", "sale_reversal"]) | Q(quantity__gt=0),
                name="stock_movement_in_kinds_positive",
            ),
            models.CheckConstraint(
                condition=~Q(kind__in=["stock_out", "sale"]) | Q(quantity__lt=0),
                name="stock_movement_out_kinds_negative",
            ),
            # Sales link to exactly one invoice line; reversals link to the sale they undo.
            models.CheckConstraint(
                condition=~Q(kind="sale") | Q(invoice_line__isnull=False), name="stock_movement_sale_has_line"
            ),
            models.CheckConstraint(
                condition=~Q(kind="sale_reversal") | (Q(reverses__isnull=False) & Q(invoice_line__isnull=False)),
                name="stock_movement_reversal_has_origin",
            ),
            models.CheckConstraint(
                condition=~Q(kind="stock_out") | ~Q(out_reason=""), name="stock_movement_out_has_reason"
            ),
            # The core guarantee: an invoice line can be deducted at most once
            # and restored at most once.
            models.UniqueConstraint(
                fields=["invoice_line", "kind"],
                condition=Q(invoice_line__isnull=False),
                name="stock_movement_one_per_line_and_kind",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.quantity:+} {self.product}"
