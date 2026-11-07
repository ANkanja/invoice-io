import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import ImmutableRecordError

POSITIVE = [MinValueValidator(Decimal("0.01"))]
MONEY = {"max_digits": 12, "decimal_places": 2}

# An allocation counts towards an invoice's "amount paid" only while its payment
# isn't reversed and the allocation hasn't been released (invoice cancelled).
ACTIVE_ALLOCATION = Q(payment__is_reversed=False, released_at__isnull=True)


class PaymentQuerySet(models.QuerySet):
    def with_allocation_totals(self):
        """Annotate `allocated` (applied to invoices) and `unallocated` (credit
        still to apply). Reversed payments have no credit."""
        from django.db.models import DecimalField, F, OuterRef, Subquery, Sum, Value
        from django.db.models.functions import Coalesce

        money = DecimalField(**MONEY)
        allocated = (
            PaymentAllocation.objects.filter(payment=OuterRef("pk"), released_at__isnull=True)
            .values("payment").annotate(s=Sum("amount")).values("s")
        )
        return self.annotate(
            allocated=Coalesce(Subquery(allocated, output_field=money), Value(Decimal("0.00")), output_field=money)
        ).annotate(unallocated=F("amount") - F("allocated"))

    def active(self):
        return self.filter(is_reversed=False)


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

    objects = PaymentQuerySet.as_manager()

    class Meta:
        ordering = ["-payment_date", "-id"]
        permissions = [("reverse_payment", "Can reverse payments")]
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

    def get_allocated(self) -> Decimal:
        if hasattr(self, "allocated"):
            return self.allocated
        total = self.allocations.filter(released_at__isnull=True).aggregate(s=models.Sum("amount"))["s"]
        return total or Decimal("0.00")

    def get_unallocated(self) -> Decimal:
        """Credit not yet applied to any invoice (0 for reversed payments)."""
        if self.is_reversed:
            return Decimal("0.00")
        return self.amount - self.get_allocated()


class PaymentAllocation(models.Model):
    """How much of a payment is applied to a given invoice.

    Never edited or deleted. If the invoice is cancelled, the allocation is
    *released* (released_at + reason): the money goes back to the payment as
    credit, which can then be applied to the replacement or another invoice.
    """

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    invoice = models.ForeignKey("invoices.Invoice", on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(**MONEY, validators=POSITIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    release_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="allocation_amount_gt_0"),
            # One live allocation per payment and invoice; released ones don't count.
            models.UniqueConstraint(fields=["payment", "invoice"], condition=Q(released_at__isnull=True),
                                    name="allocation_unique_live_payment_invoice"),
            models.CheckConstraint(condition=Q(released_at__isnull=True) | ~Q(release_reason=""),
                                   name="allocation_release_has_reason"),
        ]

    def __str__(self):
        return f"{self.amount} of payment {self.payment_id} → invoice {self.invoice_id}"

    RELEASE_FIELDS = frozenset({"released_at", "released_by_id", "release_reason"})

    def save(self, *args, **kwargs):
        if not self._state.adding and self.pk:
            attnames = [f.attname for f in self._meta.concrete_fields]
            old = type(self).objects.filter(pk=self.pk).values(*attnames).first()
            if old:
                changed = {n for n in attnames if old[n] != getattr(self, n)}
                if old["released_at"] is not None and changed:
                    raise ImmutableRecordError("A released allocation can't be changed.")
                if changed - self.RELEASE_FIELDS:
                    raise ImmutableRecordError("Payment allocations can't be edited; only released.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Payment allocations can't be deleted.")
