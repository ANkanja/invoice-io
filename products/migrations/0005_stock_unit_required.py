"""Unit system, part 3: every product now has a stock unit (filled by 0004)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0004_migrate_to_units")]

    operations = [
        migrations.AlterField(
            model_name="product", name="stock_unit",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="products.unit",
                                    help_text="What stock is counted in. Liquids: ml. Powders: g. Bars, bottles counted one by one: piece/bottle."),
        ),
    ]
