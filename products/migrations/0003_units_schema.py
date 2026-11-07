"""Unit system, part 1 (schema only, nothing removed).

* New Unit table, seeded with ml, L, g, kg and common count units.
* Old product fields are renamed to legacy_* (kept, not deleted).
* Products get a stock unit (filled in by 0004) and 3-decimal stock figures.
* New SellingOption table (ways each product is sold, with prices).
"""

from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models

UNITS = [
    # code, name, plural, symbol, dimension, factor
    ("ml", "millilitre", "millilitres", "ml", "volume", "1"),
    ("l", "litre", "litres", "L", "volume", "1000"),
    ("g", "gram", "grams", "g", "mass", "1"),
    ("kg", "kilogram", "kilograms", "kg", "mass", "1000"),
    ("piece", "piece", "pieces", "piece", "count", "1"),
    ("bottle", "bottle", "bottles", "bottle", "count", "1"),
    ("container", "container", "containers", "container", "count", "1"),
    ("carton", "carton", "cartons", "carton", "count", "1"),
    ("pack", "pack", "packs", "pack", "count", "1"),
]


def seed_units(apps, schema_editor):
    Unit = apps.get_model("products", "Unit")
    for code, name, plural, symbol, dimension, factor in UNITS:
        Unit.objects.get_or_create(code=code, defaults={
            "name": name, "plural": plural, "symbol": symbol, "dimension": dimension, "factor": Decimal(factor),
        })


class Migration(migrations.Migration):
    dependencies = [("products", "0002_product_thumbnail_and_more")]

    operations = [
        migrations.CreateModel(
            name="Unit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(max_length=20, unique=True)),
                ("name", models.CharField(help_text="Singular, e.g. litre, bottle.", max_length=30)),
                ("plural", models.CharField(help_text="E.g. litres, bottles.", max_length=30)),
                ("symbol", models.CharField(help_text="Short form shown next to numbers, e.g. L, ml, bottle.", max_length=12)),
                ("dimension", models.CharField(choices=[("volume", "Volume"), ("mass", "Mass"), ("count", "Count")], max_length=10)),
                ("factor", models.DecimalField(decimal_places=3, default=Decimal("1"), max_digits=12,
                                               help_text="Size in the dimension's smallest unit (ml or g). Count units are always 1.")),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ["dimension", "factor", "name"],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("factor__gt", 0)), name="unit_factor_gt_0"),
                    models.CheckConstraint(condition=models.Q(("dimension", "count"), _negated=True) | models.Q(("factor", 1)),
                                           name="unit_count_factor_is_1"),
                ],
            },
        ),
        migrations.RunPython(seed_units, migrations.RunPython.noop),

        # Old constraints refer to fields being renamed; drop them first.
        migrations.RemoveConstraint(model_name="product", name="product_name_size_unique_ci"),
        migrations.RemoveConstraint(model_name="product", name="product_default_price_gte_0"),
        migrations.RemoveConstraint(model_name="product", name="product_cost_price_gte_0"),
        migrations.AlterModelOptions(name="product", options={"ordering": ["name"]}),

        migrations.RenameField(model_name="product", old_name="unit", new_name="legacy_unit"),
        migrations.RenameField(model_name="product", old_name="package_size", new_name="legacy_package_size"),
        migrations.RenameField(model_name="product", old_name="default_price", new_name="legacy_price"),
        migrations.RenameField(model_name="product", old_name="cost_price", new_name="legacy_cost_price"),
        migrations.AlterField(model_name="product", name="legacy_unit",
                              field=models.CharField(blank=True, editable=False, max_length=30)),
        migrations.AlterField(model_name="product", name="legacy_package_size",
                              field=models.CharField(blank=True, editable=False, max_length=40)),
        migrations.AlterField(model_name="product", name="legacy_price",
                              field=models.DecimalField(blank=True, decimal_places=2, editable=False, max_digits=12, null=True)),
        migrations.AlterField(model_name="product", name="legacy_cost_price",
                              field=models.DecimalField(blank=True, decimal_places=2, editable=False, max_digits=12, null=True)),

        migrations.AddField(
            model_name="product", name="stock_unit",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="products.unit",
                                    help_text="What stock is counted in. Liquids: ml. Powders: g. Bars, bottles counted one by one: piece/bottle."),
        ),
        migrations.AlterField(
            model_name="product", name="stock_qty",
            field=models.DecimalField(decimal_places=3, default=Decimal("0"), editable=False, max_digits=14),
        ),
        migrations.AlterField(
            model_name="product", name="low_stock_threshold",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True,
                                      validators=[django.core.validators.MinValueValidator(Decimal("0"))],
                                      help_text="Alert at or below this amount (in the stock unit). Leave blank for no alert."),
        ),

        migrations.CreateModel(
            name="SellingOption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("method", models.CharField(choices=[("package", "Fixed pack or item"), ("measure", "By measure (per L, per kg…)")],
                                            default="package", max_length=10)),
                ("label", models.CharField(max_length=60, help_text="As shown on invoices, e.g. “500 ml bottle”, “5 L container”, “loose, per litre”.")),
                ("content_quantity", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True,
                                                         help_text="How much stock one pack holds, in the product's stock unit (e.g. 500 for a 500 ml bottle).")),
                ("price", models.DecimalField(decimal_places=2, max_digits=12,
                                              validators=[django.core.validators.MinValueValidator(Decimal("0"))])),
                ("cost_price", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True,
                                                   validators=[django.core.validators.MinValueValidator(Decimal("0"))])),
                ("is_default", models.BooleanField(default=False, help_text="Pre-selected when this product is added to an invoice.")),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("package_unit", models.ForeignKey(blank=True, limit_choices_to={"dimension": "count"}, null=True,
                                                   on_delete=django.db.models.deletion.PROTECT, related_name="+", to="products.unit",
                                                   help_text="What one pack is called: bottle, container, carton…")),
                ("price_unit", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                                 related_name="+", to="products.unit",
                                                 help_text="For measured prices: the price is per this unit (L, kg…).")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="options", to="products.product")),
            ],
            options={
                "ordering": ["product", "-is_default", "sort_order", "pk"],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("price__gte", 0)), name="selling_option_price_gte_0"),
                    models.CheckConstraint(condition=models.Q(("cost_price__isnull", True), ("cost_price__gte", 0), _connector="OR"),
                                           name="selling_option_cost_gte_0"),
                    models.CheckConstraint(
                        condition=models.Q(("method", "package"), _negated=True)
                        | models.Q(("package_unit__isnull", False), ("content_quantity__gt", 0)),
                        name="selling_option_package_complete"),
                    models.CheckConstraint(condition=models.Q(("method", "measure"), _negated=True) | models.Q(("price_unit__isnull", False)),
                                           name="selling_option_measure_has_unit"),
                    models.UniqueConstraint(condition=models.Q(("is_default", True)), fields=("product",),
                                            name="selling_option_one_default_per_product"),
                ],
            },
        ),
    ]
