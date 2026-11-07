"""Unit system: invoice lines keep a full snapshot of how they were measured
and priced (entered unit, pricing unit, conversion factor, stock quantity)."""

from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("invoices", "0002_alter_invoice_options"), ("products", "0003_units_schema")]

    operations = [
        migrations.AddField(model_name="invoiceline", name="selling_option",
                            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                                    related_name="invoice_lines", to="products.sellingoption")),
        migrations.AddField(model_name="invoiceline", name="unit_plural", field=models.CharField(blank=True, max_length=30)),
        migrations.AlterField(model_name="invoiceline", name="package_size",
                              field=models.CharField(blank=True, max_length=60, verbose_name="selling option")),
        migrations.AddField(model_name="invoiceline", name="pricing_method", field=models.CharField(blank=True, max_length=10)),
        migrations.AddField(model_name="invoiceline", name="price_unit", field=models.CharField(blank=True, max_length=30)),
        migrations.AddField(model_name="invoiceline", name="conversion_factor",
                            field=models.DecimalField(decimal_places=9, default=Decimal("1"), max_digits=18)),
        migrations.AddField(model_name="invoiceline", name="price_quantity",
                            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=18)),
        migrations.AddField(model_name="invoiceline", name="base_quantity",
                            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
        migrations.AddField(model_name="invoiceline", name="base_unit", field=models.CharField(blank=True, max_length=20)),
    ]
