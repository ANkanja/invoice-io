"""Unit system, part 2: move existing products, stock and invoice lines onto units.

Rule for each existing product (agreed with the owner, 27 Oct 2026):

* If its old "unit" is a measure (litre, ml, kg, g…), stock was counted in that
  measure. Stock moves to the smallest unit (ml or g):
  50 "litre" → 50,000 ml, and the alert level is converted the same way.
  - If it also had a size like "5 L", that becomes a pack option
    "5 L container" holding 5,000 ml at the old price.
  - Without a size, the old price becomes a measured price per that unit.
* Otherwise (bottle, piece, bar, …) it stays counted one by one: stock unit =
  that count unit (or "piece"), with one pack option of 1 at the old price.
  Stock numbers don't change.

Stock-history rows are converted with the same factor. The figures as
originally typed are kept in entered_quantity / entered_unit. Existing invoice
lines get a snapshot with factor 1, so their amounts are unchanged.
Nothing is deleted; the old values stay in the legacy_* fields.
"""

import re
from decimal import Decimal

from django.db import migrations

MEASURE_ALIASES = {
    "ml": "ml", "millilitre": "ml", "millilitres": "ml", "milliliter": "ml", "milliliters": "ml",
    "l": "l", "lt": "l", "ltr": "l", "ltrs": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l",
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
}
COUNT_ALIASES = {
    "piece": "piece", "pieces": "piece", "pc": "piece", "pcs": "piece",
    "bottle": "bottle", "bottles": "bottle", "container": "container", "containers": "container",
    "carton": "carton", "cartons": "carton", "pack": "pack", "packs": "pack", "packet": "pack", "packets": "pack",
}
SIZE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([a-zA-Z]+)\s*$")


def _parse_size(text):
    match = SIZE_RE.match(text or "")
    if not match:
        return None
    unit = MEASURE_ALIASES.get(match.group(2).lower())
    return (Decimal(match.group(1).replace(",", ".")), unit) if unit else None


def forwards(apps, schema_editor):
    Unit = apps.get_model("products", "Unit")
    Product = apps.get_model("products", "Product")
    Option = apps.get_model("products", "SellingOption")
    Movement = apps.get_model("inventory", "StockMovement")
    Line = apps.get_model("invoices", "InvoiceLine")
    units = {u.code: u for u in Unit.objects.all()}

    for product in Product.objects.all():
        legacy_unit = (product.legacy_unit or "").strip().lower()
        measure = MEASURE_ALIASES.get(legacy_unit)
        price = product.legacy_price if product.legacy_price is not None else Decimal("0")

        if measure:
            sold_in = units[measure]
            base = units["ml"] if sold_in.dimension == "volume" else units["g"]
            factor = sold_in.factor  # e.g. litre → 1000 ml
            size = _parse_size(product.legacy_package_size)
            if size and units[size[1]].dimension == sold_in.dimension:
                qty, size_unit = size
                Option.objects.create(
                    product=product, method="package",
                    label=f"{product.legacy_package_size.strip()} {'container' if base.code == 'ml' else 'pack'}",
                    package_unit=units["container"] if base.code == "ml" else units["pack"],
                    content_quantity=qty * units[size_unit].factor,
                    price=price, cost_price=product.legacy_cost_price, is_default=True,
                )
            else:
                Option.objects.create(
                    product=product, method="measure", label=f"per {sold_in.name}", price_unit=sold_in,
                    price=price, cost_price=product.legacy_cost_price, is_default=True,
                )
        else:
            count_unit = units[COUNT_ALIASES.get(legacy_unit, "piece")]
            base, factor = count_unit, Decimal("1")
            label = " ".join(x for x in [product.legacy_package_size.strip(), product.legacy_unit.strip()] if x) or count_unit.name
            Option.objects.create(
                product=product, method="package", label=label[:60], package_unit=count_unit,
                content_quantity=Decimal("1"), price=price, cost_price=product.legacy_cost_price, is_default=True,
            )

        product.stock_unit = base
        product.stock_qty = product.stock_qty * factor
        if product.low_stock_threshold is not None:
            product.low_stock_threshold = product.low_stock_threshold * factor
        product.save(update_fields=["stock_unit", "stock_qty", "low_stock_threshold"])

        original_label = product.legacy_unit or base.name
        for movement in Movement.objects.filter(product=product):
            # Converting units once, during this migration, is the only time
            # ledger rows are rewritten; the originals are kept alongside.
            Movement.objects.filter(pk=movement.pk).update(
                entered_quantity=movement.quantity,
                entered_unit=original_label,
                quantity=movement.quantity * factor,
                balance_after=movement.balance_after * factor,
            )
        for line in Line.objects.filter(product=product):
            Line.objects.filter(pk=line.pk).update(base_quantity=line.quantity * factor, base_unit=base.symbol)

    # Every existing invoice line: priced as entered (factor 1), so totals are unchanged.
    # Free-text lines keep their typed unit for the quantity ("2 litres") but no
    # "per …" label, since the typed unit may already be plural.
    for line in Line.objects.all():
        Line.objects.filter(pk=line.pk).update(
            price_quantity=line.quantity, conversion_factor=Decimal("1"),
            price_unit=line.unit if line.product_id else "", unit_plural=line.unit,
            pricing_method="package" if line.product_id else "",
        )


def backwards(apps, schema_editor):
    Product = apps.get_model("products", "Product")
    Option = apps.get_model("products", "SellingOption")
    Movement = apps.get_model("inventory", "StockMovement")
    for product in Product.objects.select_related("stock_unit"):
        legacy = MEASURE_ALIASES.get((product.legacy_unit or "").strip().lower())
        if legacy and product.stock_unit:
            factor = {"l": Decimal(1000), "kg": Decimal(1000)}.get(legacy, Decimal(1))
            product.stock_qty = product.stock_qty / factor
            if product.low_stock_threshold is not None:
                product.low_stock_threshold = product.low_stock_threshold / factor
            product.save(update_fields=["stock_qty", "low_stock_threshold"])
            for m in Movement.objects.filter(product=product):
                Movement.objects.filter(pk=m.pk).update(quantity=m.quantity / factor, balance_after=m.balance_after / factor)
    Option.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0003_units_schema"),
        ("inventory", "0003_units_schema"),
        ("invoices", "0003_line_units_schema"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
