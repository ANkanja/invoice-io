"""Seed the standard payment methods. These are generic options, not business
data; they can be renamed, re-ordered or deactivated afterwards."""

from django.db import migrations

METHODS = [
    ("Cash", False, 10),
    ("M-Pesa", True, 20),
    ("Bank transfer", True, 30),
    ("Cheque", True, 40),
]


def seed(apps, schema_editor):
    PaymentMethod = apps.get_model("payments", "PaymentMethod")
    for name, needs_ref, order in METHODS:
        PaymentMethod.objects.get_or_create(
            name=name, defaults={"requires_reference": needs_ref, "sort_order": order}
        )


def unseed(apps, schema_editor):
    PaymentMethod = apps.get_model("payments", "PaymentMethod")
    PaymentMethod.objects.filter(name__in=[m[0] for m in METHODS], payments__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("payments", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
