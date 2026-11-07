"""Unit system: stock movements store 3-decimal quantities in the product's
stock unit, remember what was typed, and gain a 'customer return' type."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0002_stockmovement_submission_token_and_more"), ("products", "0003_units_schema")]

    operations = [
        migrations.AlterField(model_name="stockmovement", name="quantity",
                              field=models.DecimalField(decimal_places=3, max_digits=14)),
        migrations.AlterField(model_name="stockmovement", name="balance_after",
                              field=models.DecimalField(decimal_places=3, max_digits=14)),
        migrations.AddField(model_name="stockmovement", name="entered_quantity",
                            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
        migrations.AddField(model_name="stockmovement", name="entered_unit",
                            field=models.CharField(blank=True, max_length=60)),
        migrations.AlterField(
            model_name="stockmovement", name="kind",
            field=models.CharField(choices=[
                ("stock_in", "Stock in"), ("stock_out", "Stock out"), ("adjustment", "Count adjustment"),
                ("sale", "Sale (invoice issued)"), ("sale_reversal", "Sale reversed (invoice cancelled or amended)"),
                ("return", "Customer return")], max_length=20),
        ),
        migrations.RemoveConstraint(model_name="stockmovement", name="stock_movement_in_kinds_positive"),
        migrations.AddConstraint(
            model_name="stockmovement",
            constraint=models.CheckConstraint(
                condition=models.Q(("kind__in", ["stock_in", "sale_reversal", "return"]), _negated=True) | models.Q(("quantity__gt", 0)),
                name="stock_movement_in_kinds_positive"),
        ),
    ]
