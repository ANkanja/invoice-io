from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q

from .validators import logo_upload_path, phone_validator, validate_image_upload


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ImmutableRecordError(Exception):
    """Raised when code tries to change or delete a record that must never change."""


class AppendOnlyModel(models.Model):
    """Ledger-style records: can be created, never edited or deleted.

    Note: bulk QuerySet.update()/delete() bypass model methods; project code never
    uses them on these models, and the admin is read-only for them.
    """

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableRecordError(f"{self.__class__.__name__} records cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError(f"{self.__class__.__name__} records cannot be deleted.")


prefix_validator = RegexValidator(
    r"^[A-Z0-9]{1,10}$", "Use 1–10 capital letters or digits, e.g. INV."
)


class BusinessSettings(TimeStampedModel):
    """Single-row table holding the business identity and app-wide preferences.

    Every value is entered by the owner on the Settings page. Nothing here is
    pre-filled with invented business details.
    """

    # Identity
    name = models.CharField("business name", max_length=150, blank=True)
    logo = models.ImageField(upload_to=logo_upload_path, blank=True, validators=[validate_image_upload])
    phone = models.CharField(max_length=30, blank=True, validators=[phone_validator])
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    kra_pin = models.CharField("KRA PIN", max_length=20, blank=True)
    registration_number = models.CharField(max_length=50, blank=True)

    # Invoicing
    currency = models.CharField(max_length=3, default="KES")
    invoice_prefix = models.CharField(max_length=10, default="INV", validators=[prefix_validator])
    invoice_number_padding = models.PositiveSmallIntegerField(
        default=4, validators=[MinValueValidator(3), MaxValueValidator(8)],
        help_text="Digits in the running number: 4 gives INV-2026-0001.",
    )
    reset_numbering_yearly = models.BooleanField(
        default=True, help_text="Include the year and restart at 1 each January."
    )
    default_payment_terms_days = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Days until due. Leave blank for no due date."
    )
    default_payment_terms = models.TextField("default payment terms text", blank=True)
    default_invoice_notes = models.TextField(blank=True)
    payment_instructions = models.TextField(
        blank=True, help_text="E.g. M-Pesa till/paybill or bank details, printed on invoices."
    )

    # Tax (off until configured — see planning decision D1)
    tax_enabled = models.BooleanField(default=False)
    tax_label = models.CharField(max_length=20, default="VAT")
    tax_rate = models.DecimalField(
        "tax rate (%)", max_digits=5, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    prices_include_tax = models.BooleanField(
        default=False, help_text="Tick if your catalogue prices already include tax."
    )

    # Stock
    allow_negative_stock = models.BooleanField(
        default=False,
        help_text="If off, an invoice cannot be issued when it would take stock below zero.",
    )

    class Meta:
        verbose_name = "business settings"
        verbose_name_plural = "business settings"
        constraints = [
            models.CheckConstraint(condition=Q(pk=1), name="business_settings_singleton"),
            models.CheckConstraint(
                condition=Q(tax_rate__gte=0) & Q(tax_rate__lte=100), name="business_settings_tax_rate_range"
            ),
        ]

    def __str__(self):
        return self.name or "Business settings"

    def save(self, *args, **kwargs):
        self.pk = 1  # always the same row
        if self.created_at is None:
            # A fresh instance overwriting the existing row keeps its creation time.
            self.created_at = type(self).objects.filter(pk=1).values_list("created_at", flat=True).first()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Business settings cannot be deleted.")

    @classmethod
    def load(cls) -> "BusinessSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def is_configured(self) -> bool:
        return bool(self.name and self.phone)

    def effective_tax_rate(self) -> Decimal:
        return self.tax_rate if self.tax_enabled else Decimal("0.00")

    def snapshot(self) -> dict:
        """Seller details frozen onto an invoice when it is issued."""
        return {
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "address": self.address,
            "kra_pin": self.kra_pin,
            "registration_number": self.registration_number,
            "logo": self.logo.name if self.logo else "",
            "payment_instructions": self.payment_instructions,
        }


class AuditLog(AppendOnlyModel):
    """Who did what to which record, and why. Append-only."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="audit_entries")
    action = models.CharField(max_length=50)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.CharField(max_length=64)
    object_repr = models.CharField(max_length=200)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor} {self.action} {self.object_repr}"

    @classmethod
    def record(cls, *, actor, action: str, obj, changes: dict | None = None, reason: str = "") -> "AuditLog":
        return cls.objects.create(
            actor=actor,
            action=action,
            content_type=ContentType.objects.get_for_model(obj),
            object_id=str(obj.pk),
            object_repr=str(obj)[:200],
            changes=changes or {},
            reason=reason,
        )


def form_changes(form, before: dict) -> dict:
    """{field: [old, new]} for fields a ModelForm actually changed — for AuditLog."""
    changes = {}
    for name in form.changed_data:
        if name not in before:
            continue
        old, new = before[name], getattr(form.instance, name, None)
        if str(old) != str(new):
            changes[name] = [str(old), str(new)]
    return changes
