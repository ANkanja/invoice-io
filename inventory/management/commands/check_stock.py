from django.core.management.base import BaseCommand, CommandError

from inventory.services import find_stock_discrepancies


class Command(BaseCommand):
    help = "Verify every product's stock quantity matches its stock-movement ledger."

    def handle(self, *args, **options):
        problems = find_stock_discrepancies()
        if not problems:
            self.stdout.write(self.style.SUCCESS("Stock OK: every product matches its ledger."))
            return
        for product, cached, expected in problems:
            self.stderr.write(f"{product} (id {product.pk}): stored {cached}, ledger says {expected}")
        raise CommandError(f"{len(problems)} product(s) out of step with the stock ledger.")
