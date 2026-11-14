"""
Restock planning: what to buy each month, and what was actually bought.

* A RestockPlan covers one month and may have a budget.
* Each RestockLine is a product (quantities in its friendly unit: L, kg,
  bottles…) or another item you buy (raw materials, containers, transport),
  with the planned quantity and cost.
* Each RestockPurchase is one real purchase against a line. Purchases of
  stock-tracked products are recorded as Stock in at the same time. Purchases
  are never edited or deleted: a mistake is reversed with a reason (which also
  takes the stock back out).
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from core.models import ImmutableRecordError, TimeStampedModel
from core.money import quantize

NON_NEGATIVE = [MinValueValidator(Decimal("0"))]


class RestockPlan(TimeStampedModel):
    month = models.DateField(unique=True, help_text="First day of the month the plan is for.")
    budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=NON_NEGATIVE,
                                 help_text="Money set aside for restocking this month. Optional.")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-month"]
        constraints = [
            models.CheckConstraint(condition=Q(budget__isnull=True) | Q(budget__gte=0), name="restock_plan_budget_gte_0"),
        ]

    def __str__(self):
        return f"Restock plan {self.month:%B %Y}"


class RestockLine(models.Model):
    plan = models.ForeignKey(RestockPlan, on_delete=models.PROTECT, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=150)
    unit_label = models.CharField(max_length=30, blank=True, help_text="What the quantity is counted in: L, kg, bags…")
    suggested_qty = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    monthly_sales = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True,
                                        help_text="Average sold per month when the plan was made (in unit_label).")
    stock_at_planning = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    planned_qty = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"), validators=NON_NEGATIVE)
    planned_unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                            validators=NON_NEGATIVE)

    class Meta:
        ordering = ["plan", "position", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(planned_qty__gte=0), name="restock_line_qty_gte_0"),
            models.CheckConstraint(condition=Q(planned_unit_cost__isnull=True) | Q(planned_unit_cost__gte=0),
                                   name="restock_line_cost_gte_0"),
        ]

    def __str__(self):
        return self.description

    @property
    def planned_total(self) -> Decimal:
        if self.planned_unit_cost is None:
            return Decimal("0.00")
        return quantize(self.planned_qty * self.planned_unit_cost)


class RestockPurchase(models.Model):
    REVERSAL_FIELDS = frozenset({"is_reversed", "reversed_at", "reversed_by_id", "reversal_reason", "reversal_movement_id"})

    line = models.ForeignKey(RestockLine, on_delete=models.PROTECT, related_name="purchases")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, validators=NON_NEGATIVE)
    purchase_date = models.DateField()
    reference = models.CharField(max_length=80, blank=True, help_text="Supplier, receipt number…")
    stock_movement = models.OneToOneField("inventory.StockMovement", on_delete=models.PROTECT, null=True, blank=True,
                                          related_name="restock_purchase")
    is_reversed = models.BooleanField(default=False)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    reversal_reason = models.CharField(max_length=200, blank=True)
    reversal_movement = models.OneToOneField("inventory.StockMovement", on_delete=models.PROTECT, null=True,
                                             blank=True, related_name="restock_reversal")
    submission_token = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["purchase_date", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="restock_purchase_qty_gt_0"),
            models.CheckConstraint(condition=Q(amount_paid__gte=0), name="restock_purchase_amount_gte_0"),
        ]

    def __str__(self):
        return f"{self.line.description}: {self.quantity} for {self.amount_paid}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            fields = kwargs.get("update_fields")
            if not fields or not set(fields) <= self.REVERSAL_FIELDS:
                raise ImmutableRecordError("Purchases can't be changed. Reverse it and record the right one.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Purchases are kept for your records. Reverse it instead.")
