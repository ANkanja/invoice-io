from django.db import models

from core.models import TimeStampedModel
from core.validators import phone_validator


class Customer(TimeStampedModel):
    """A saved customer: an individual or an institution (school, hospital…).
    The type decides which prices and payment terms new invoices start with.

    One-time customers don't need a record — an invoice stores its own copy of
    the customer's details either way.
    """

    class Kind(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        # Stored as "organization" (the value existing records already have).
        INSTITUTION = "organization", "Institution"

    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.INDIVIDUAL)
    name = models.CharField(max_length=150)
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=30, blank=True, validators=[phone_validator])
    email = models.EmailField(blank=True)
    billing_address = models.TextField(blank=True)
    tax_pin = models.CharField("KRA PIN / tax ID", max_length=30, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["name"]), models.Index(fields=["phone"])]

    def __str__(self):
        return self.name
