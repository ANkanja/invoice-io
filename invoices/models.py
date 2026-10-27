from decimal import Decimal

from django.apps import apps
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.models import ImmutableRecordError, TimeStampedModel
from core.money import ZERO, quantize

MONEY = {"max_digits": 12, "decimal_places": 2}
NON_NEGATIVE = [MinValueValidator(Decimal("0"))]


class InvoiceSequence(models.Model):
    """Running counter per prefix and year. Incremented inside a transaction
    with a database-level F() update, so two people issuing at once can never
    get the same number. `year` is 0 when yearly reset is switched off."""

    prefix = models.CharField(max_length=10)
    year = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["prefix", "year"], name="invoice_sequence_unique")]

    def __str__(self):
        return f"{self.prefix}/{self.year}: {self.last_number}"


class PaymentState(models.TextChoices):
    DRAFT = "draft", "Draft"
    UNPAID = "unpaid", "Unpaid"
    PARTIAL = "partial", "Partially paid"
    PAID = "paid", "Paid"
    OVERDUE = "overdue", "Overdue"
    CANCELLED = "cancelled", "Cancelled"


class InvoiceQuerySet(models.QuerySet):
    def with_payment_totals(self):
        """Annotate amount_paid (non-reversed allocations) and balance."""
        Allocation = apps.get_model("payments", "PaymentAllocation")
        paid_sq = (
            Allocation.objects.filter(invoice=OuterRef("pk"), payment__is_reversed=False)
            .values("invoice")
            .annotate(s=Sum("amount"))
            .values("s")
        )
        money = DecimalField(**MONEY)
        return self.annotate(
            amount_paid=Coalesce(Subquery(paid_sq, output_field=money), Value(Decimal("0.00")), output_field=money)
        ).annotate(balance=F("total") - F("amount_paid"))

    def issued(self):
        return self.filter(status=Invoice.Status.ISSUED)

    def outstanding(self):
        return self.issued().with_payment_totals().filter(balance__gt=0)

    def overdue(self, today=None):
        today = today or timezone.localdate()
        return self.outstanding().filter(due_date__lt=today)


class Invoice(TimeStampedModel):
    """An invoice. Drafts can be edited freely; once ISSUED the financial content
    is frozen (enforced in save()). Corrections = cancel with a reason, then
    issue a replacement that links back via `replaces`."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        CANCELLED = "cancelled", "Cancelled"

    # Fields that may still change after issue (cancellation + eTIMS cross-reference).
    EDITABLE_WHEN_ISSUED = frozenset(
        {"status", "cancelled_at", "cancelled_by_id", "cancel_reason", "etims_reference", "updated_at"}
    )

    number = models.CharField(max_length=40, unique=True, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)

    # Customer: optional link + snapshot that is always filled in.
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="invoices"
    )
    customer_name = models.CharField(max_length=150)
    customer_contact_person = models.CharField(max_length=120, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    customer_email = models.EmailField(blank=True)
    customer_address = models.TextField(blank=True)
    customer_tax_pin = models.CharField(max_length=30, blank=True)

    # Seller details frozen at issue (from BusinessSettings.snapshot()).
    business_snapshot = models.JSONField(default=dict, blank=True)

    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="KES")
    payment_terms = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    invoice_discount = models.DecimalField(**MONEY, default=Decimal("0.00"), validators=NON_NEGATIVE)
    tax_label = models.CharField(max_length=20, blank=True)
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    prices_include_tax = models.BooleanField(default=False)

    # Calculated by invoices.calculations; stored so issued invoices reproduce exactly.
    subtotal = models.DecimalField(**MONEY, default=Decimal("0.00"))
    tax_total = models.DecimalField(**MONEY, default=Decimal("0.00"))
    total = models.DecimalField(**MONEY, default=Decimal("0.00"))

    etims_reference = models.CharField("eTIMS invoice number", max_length=60, blank=True)
    replaces = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="replaced_by",
        help_text="The cancelled invoice this one corrects.",
    )

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cancel_reason = models.TextField(blank=True)

    objects = InvoiceQuerySet.as_manager()

    class Meta:
        ordering = ["-issue_date", "-id"]
        indexes = [
            models.Index(fields=["status", "issue_date"]),
            models.Index(fields=["customer", "issue_date"]),
            models.Index(fields=["due_date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status="draft") | Q(number__isnull=False), name="invoice_number_required_unless_draft"
            ),
            models.CheckConstraint(
                condition=~Q(status="cancelled") | ~Q(cancel_reason=""), name="invoice_cancel_reason_required"
            ),
            models.CheckConstraint(
                condition=Q(due_date__isnull=True) | Q(due_date__gte=F("issue_date")),
                name="invoice_due_not_before_issue",
            ),
            models.CheckConstraint(
                condition=Q(invoice_discount__gte=0) & Q(subtotal__gte=0) & Q(tax_total__gte=0) & Q(total__gte=0),
                name="invoice_amounts_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(tax_rate__gte=0) & Q(tax_rate__lte=100), name="invoice_tax_rate_range"
            ),
        ]

    def __str__(self):
        return f"{self.number or 'DRAFT #%s' % self.pk} — {self.customer_name}"

    # --- integrity guards -------------------------------------------------
    def save(self, *args, **kwargs):
        if not self._state.adding and self.pk:
            attnames = [f.attname for f in self._meta.concrete_fields]
            old = type(self).objects.filter(pk=self.pk).values(*attnames).first()
            if old and old["status"] != self.Status.DRAFT:
                changed = {name for name in attnames if old[name] != getattr(self, name)}
                if old["status"] == self.Status.CANCELLED:
                    allowed = {"updated_at"}
                else:
                    allowed = self.EDITABLE_WHEN_ISSUED
                    if self.status not in (self.Status.ISSUED, self.Status.CANCELLED):
                        raise ImmutableRecordError("An issued invoice cannot return to draft.")
                illegal = changed - allowed
                if illegal:
                    raise ImmutableRecordError(
                        f"Invoice {old['number']} is {old['status']}; cannot change: {', '.join(sorted(illegal))}."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ImmutableRecordError("Only draft invoices can be deleted. Cancel issued invoices instead.")
        return super().delete(*args, **kwargs)

    # --- derived values ---------------------------------------------------
    @property
    def is_editable(self) -> bool:
        return self.status == self.Status.DRAFT

    @property
    def line_discount_total(self) -> Decimal:
        return quantize(sum((line.line_discount for line in self.lines.all()), ZERO))

    def get_amount_paid(self) -> Decimal:
        if hasattr(self, "amount_paid"):
            return self.amount_paid
        Allocation = apps.get_model("payments", "PaymentAllocation")
        total = Allocation.objects.filter(invoice=self, payment__is_reversed=False).aggregate(s=Sum("amount"))["s"]
        return quantize(total or ZERO)

    def get_balance(self) -> Decimal:
        return quantize(self.total - self.get_amount_paid())

    def payment_state(self, today=None) -> str:
        if self.status == self.Status.DRAFT:
            return PaymentState.DRAFT
        if self.status == self.Status.CANCELLED:
            return PaymentState.CANCELLED
        today = today or timezone.localdate()
        paid = self.get_amount_paid()
        if self.total - paid <= 0:
            return PaymentState.PAID
        if self.due_date and today > self.due_date:
            return PaymentState.OVERDUE
        if paid > 0:
            return PaymentState.PARTIAL
        return PaymentState.UNPAID


class InvoiceLine(models.Model):
    """One row on an invoice. Description, unit and prices are copies taken
    when the line was added, so later catalogue edits never alter invoices."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    product = models.ForeignKey(
        "products.Product", on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines"
    )
    description = models.CharField(max_length=255)
    unit = models.CharField(max_length=30, blank=True)
    package_size = models.CharField(max_length=40, blank=True)
    catalogue_price = models.DecimalField(
        **MONEY, null=True, blank=True, help_text="Product's default price when the line was added."
    )
    quantity = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    unit_price = models.DecimalField(**MONEY, validators=NON_NEGATIVE)
    line_discount = models.DecimalField(**MONEY, default=Decimal("0.00"), validators=NON_NEGATIVE)
    line_total = models.DecimalField(**MONEY, default=Decimal("0.00"))

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="invoice_line_quantity_gt_0"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="invoice_line_unit_price_gte_0"),
            models.CheckConstraint(condition=Q(line_discount__gte=0), name="invoice_line_discount_gte_0"),
            models.CheckConstraint(condition=Q(line_total__gte=0), name="invoice_line_total_gte_0"),
        ]

    def __str__(self):
        return f"{self.description} × {self.quantity}"

    def _parent_is_draft(self) -> bool:
        return Invoice.objects.filter(pk=self.invoice_id, status=Invoice.Status.DRAFT).exists()

    def save(self, *args, **kwargs):
        if not self._parent_is_draft():
            raise ImmutableRecordError("Lines can only be changed while the invoice is a draft.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self._parent_is_draft():
            raise ImmutableRecordError("Lines can only be removed while the invoice is a draft.")
        return super().delete(*args, **kwargs)

    @property
    def is_price_overridden(self) -> bool:
        return self.catalogue_price is not None and self.unit_price != self.catalogue_price
