import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import AppendOnlyModel, ImmutableRecordError

POSITIVE = [MinValueValidator(Decimal("0.01"))]


class PaymentMethod(models.Model):
    """Configurable list (Cash, M-Pesa, …). Seeded by a data migration; editable."""

    name = models.CharField(max_length=40, unique=True)
    requires_reference = models.BooleanField(
        default=False, help_text="E.g. M-Pesa code or bank reference must be entered."
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Payment(models.Model):
    """Money actually received, recorded manually. Never deleted: mistakes are
    reversed (is_reversed + reason), which removes them from all balances.

    One payment can be split across several invoices via PaymentAllocation
    (e.g. a school paying three invoices with one transfer).
    """

    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="payments"
    )
    payer_name = models.CharField(max_length=150)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=POSITIVE)
    payment_date = models.DateField(default=timezone.localdate)
    method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name="payments")
    reference = models.CharField(max_length=60, blank=True, help_text="M-Pesa code, bank ref, cheque no.")
    notes = models.TextField(blank=True)

    # One token per rendered form: a double-click or browser resubmit hits the
    # unique constraint instead of recording the payment twice.
    submission_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    is_reversed = models.BooleanField(default=False)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    reversal_reason = models.TextField(blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        indexes = [models.Index(fields=["payment_date"]), models.Index(fields=["customer", "payment_date"])]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_gt_0"),
            # The same M-Pesa / bank reference can't be recorded twice (unless the
            # earlier entry was reversed).
            models.UniqueConstraint(
                fields=["method", "reference"],
                condition=~Q(reference="") & Q(is_reversed=False),
                name="payment_unique_reference_per_method",
            ),
            models.CheckConstraint(
                condition=Q(is_reversed=False) | (Q(reversed_at__isnull=False) & ~Q(reversal_reason="")),
                name="payment_reversal_complete",
            ),
        ]

    def __str__(self):
        return f"{self.payment_date} {self.payer_name} {self.amount}"

    # After a payment is recorded, only the reversal fields and notes may change.
    EDITABLE_AFTER_CREATE = frozenset({"is_reversed", "reversed_at", "reversed_by_id", "reversal_reason", "notes"})

    def save(self, *args, **kwargs):
        self.reference = (self.reference or "").strip().upper()
        if not self._state.adding and self.pk:
            attnames = [f.attname for f in self._meta.concrete_fields]
            old = type(self).objects.filter(pk=self.pk).values(*attnames).first()
            if old:
                if old["is_reversed"] and not self.is_reversed:
                    raise ImmutableRecordError("A reversed payment cannot be un-reversed. Record a new payment.")
                changed = {n for n in attnames if old[n] != getattr(self, n)}
                illegal = changed - self.EDITABLE_AFTER_CREATE
                if illegal:
                    raise ImmutableRecordError(
                        f"Recorded payments cannot be edited ({', '.join(sorted(illegal))}). Reverse and re-enter instead."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Payments cannot be deleted. Reverse the payment instead.")


class PaymentAllocation(AppendOnlyModel):
    """How much of a payment is applied to a given invoice."""

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    invoice = models.ForeignKey("invoices.Invoice", on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=POSITIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="allocation_amount_gt_0"),
            models.UniqueConstraint(fields=["payment", "invoice"], name="allocation_unique_payment_invoice"),
        ]

    def __str__(self):
        return f"{self.amount} of payment {self.payment_id} → invoice {self.invoice_id}"
