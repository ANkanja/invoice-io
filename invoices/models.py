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
            Allocation.objects.filter(invoice=OuterRef("pk"), payment__is_reversed=False, released_at__isnull=True)
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

    def money_totals(self) -> dict:
        """{'count', 'total', 'paid', 'balance'} for these invoices (expects
        with_payment_totals()). Each sum is its own query: summing the computed
        balance directly gave wrong results on SQLite."""
        zero = Decimal("0.00")
        total = self.aggregate(s=Sum("total"))["s"] or zero
        paid = self.aggregate(s=Sum("amount_paid"))["s"] or zero
        return {"count": self.count(), "total": total, "paid": paid, "balance": total - paid}

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
    # Individual or institution ("organization"): which prices and payment terms
    # the invoice starts with. Blank on invoices made before customer types were used.
    customer_kind = models.CharField(
        max_length=20, blank=True, choices=[("individual", "Individual"), ("organization", "Institution")],
    )

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

    # Amendments: issued invoices can be corrected; each correction archives the
    # previous version as an InvoiceRevision and bumps this number.
    revision = models.PositiveSmallIntegerField(default=1)
    last_amended_at = models.DateTimeField(null=True, blank=True)
    last_amended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    objects = InvoiceQuerySet.as_manager()

    class Meta:
        ordering = ["-issue_date", "-id"]
        permissions = [
            ("issue_invoice", "Can issue invoices"),
            ("cancel_invoice", "Can cancel issued invoices"),
            ("amend_invoice", "Can amend issued invoices"),
        ]
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
        # invoices.services.amend_invoice sets _amending after archiving the old version.
        if not self._state.adding and self.pk and not getattr(self, "_amending", False):
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
        total = Allocation.objects.filter(invoice=self, payment__is_reversed=False,
                                          released_at__isnull=True).aggregate(s=Sum("amount"))["s"]
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

    @property
    def payment_state_display(self) -> str:
        return PaymentState(self.payment_state()).label


class CurrentLineManager(models.Manager):
    """invoice.lines and InvoiceLine.objects show only the invoice's current
    version; lines superseded by an amendment stay in the database (their stock
    history points at them) and are reachable via InvoiceLine.all_objects."""

    def get_queryset(self):
        return super().get_queryset().filter(is_current=True)


class InvoiceLine(models.Model):
    """One row on an invoice. Description, unit and prices are copies taken
    when the line was added, so later catalogue edits never alter invoices."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    product = models.ForeignKey(
        "products.Product", on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines"
    )
    selling_option = models.ForeignKey(
        "products.SellingOption", on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines"
    )
    description = models.CharField(max_length=255)
    # --- snapshot of how the line was measured and priced (never recomputed from the product) ---
    # quantity (below) and unit are what was entered: 1500 + "ml", or 5 + "bottle".
    unit = models.CharField(max_length=30, blank=True)
    unit_plural = models.CharField(max_length=30, blank=True)
    package_size = models.CharField("selling option", max_length=60, blank=True)  # e.g. "500 ml bottle"
    pricing_method = models.CharField(max_length=10, blank=True)  # package / measure / "" (free text)
    price_unit = models.CharField(max_length=30, blank=True)  # the price is per this: "L", "bottle"
    conversion_factor = models.DecimalField(max_digits=18, decimal_places=9, default=Decimal("1"))
    price_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))  # in price units
    base_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)  # stock units
    base_unit = models.CharField(max_length=20, blank=True)
    catalogue_price = models.DecimalField(
        **MONEY, null=True, blank=True, help_text="Product's default price when the line was added."
    )
    quantity = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    unit_price = models.DecimalField(**MONEY, validators=NON_NEGATIVE)
    line_discount = models.DecimalField(**MONEY, default=Decimal("0.00"), validators=NON_NEGATIVE)
    line_total = models.DecimalField(**MONEY, default=Decimal("0.00"))
    is_current = models.BooleanField(default=True, db_index=True)

    objects = CurrentLineManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["position", "id"]
        base_manager_name = "all_objects"
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
        # New lines may be added to an issued invoice only by amend_invoice (sets _amending).
        allowed = self._state.adding and getattr(self, "_amending", False)
        if not allowed and not self._parent_is_draft():
            raise ImmutableRecordError("Lines can only be changed while the invoice is a draft.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self._parent_is_draft():
            raise ImmutableRecordError("Lines can only be removed while the invoice is a draft.")
        return super().delete(*args, **kwargs)

    @property
    def quantity_display(self) -> str:
        """'1,500 ml', '5 bottles', '1 bottle', '2 litres'."""
        from core.money import format_quantity

        qty = format_quantity(self.quantity)
        if self.pricing_method == "package" and self.unit:
            label = self.unit if self.quantity == 1 else (self.unit_plural or self.unit)
            return f"{qty} {label}"
        return f"{qty} {self.unit}".strip()

    @property
    def price_display_unit(self) -> str:
        """'per L', 'per bottle', or '' for plain lines."""
        return f"per {self.price_unit}" if self.price_unit else ""

    @property
    def is_price_overridden(self) -> bool:
        return self.catalogue_price is not None and self.unit_price != self.catalogue_price


class InvoiceRevision(models.Model):
    """A previous version of an issued invoice, archived when it was amended.
    Append-only: the snapshot holds the full header, lines and totals."""

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="revisions")
    revision_no = models.PositiveSmallIntegerField()
    snapshot = models.JSONField()
    reason = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-revision_no"]
        constraints = [models.UniqueConstraint(fields=["invoice", "revision_no"], name="invoice_revision_unique")]

    def __str__(self):
        return f"{self.invoice.number} revision {self.revision_no}"

    def as_invoice(self):
        """(Invoice, [InvoiceLine]) rebuilt from the snapshot, unsaved, for
        showing or printing this earlier version."""
        import datetime

        data = dict(self.snapshot)
        line_data = data.pop("lines", [])
        fields = {f.attname: f for f in Invoice._meta.concrete_fields}
        values = {}
        for key, value in data.items():
            field = fields.get(key)
            if field is None or value is None:
                continue
            if isinstance(field, models.DecimalField):
                value = Decimal(value)
            elif isinstance(field, models.DateField) and not isinstance(field, models.DateTimeField):
                value = datetime.date.fromisoformat(value)
            values[key] = value
        invoice = Invoice(pk=self.invoice_id, **values)
        invoice.revision = self.revision_no
        line_fields = {f.attname: f for f in InvoiceLine._meta.concrete_fields}
        lines = []
        for row in line_data:
            kwargs = {}
            for key, value in row.items():
                field = line_fields.get(key)
                if field is None or value is None:
                    continue
                kwargs[key] = Decimal(value) if isinstance(field, models.DecimalField) else value
            lines.append(InvoiceLine(invoice=invoice, **kwargs))
        return invoice, lines

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableRecordError("Invoice revisions cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Invoice revisions cannot be deleted.")
