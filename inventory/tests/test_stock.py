from decimal import Decimal as D

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import ImmutableRecordError
from core.testing import configure_business, make_product, make_user, unit
from inventory.models import StockMovement
from inventory.services import (
    InsufficientStockError, StockError, find_stock_discrepancies, record_stock_count, record_stock_in,
    record_stock_out,
)
from products.models import Product


class StockServiceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business()
        self.p = make_product(low_stock_threshold=D("5"))

    def qty(self):
        self.p.refresh_from_db()
        return self.p.stock_qty

    def test_stock_in_out_and_balance(self):
        m1 = record_stock_in(self.p, "20", self.user, unit_cost="45.50", reference="Delivery 1")
        m2 = record_stock_out(self.p, "3", self.user, reason="damaged", notes="Leaking caps")
        self.assertEqual((m1.balance_after, m2.balance_after), (D("20"), D("17")))
        self.assertEqual(m2.quantity, D("-3"))
        self.assertEqual(self.qty(), D("17"))

    def test_rejects_bad_quantities(self):
        for bad in ["0", "-1", "1.0005"]:  # stock units allow 3 decimals (e.g. 1.5 ml)
            with self.subTest(bad=bad), self.assertRaises(StockError):
                record_stock_in(self.p, bad, self.user)

    def test_stock_out_needs_reason_and_stock(self):
        record_stock_in(self.p, "2", self.user)
        with self.assertRaises(StockError):
            record_stock_out(self.p, "1", self.user, reason="")
        with self.assertRaises(InsufficientStockError):
            record_stock_out(self.p, "3", self.user, reason="expired")
        self.assertEqual(self.qty(), D("2"))

    def test_count_adjustment(self):
        record_stock_in(self.p, "10", self.user)
        m = record_stock_count(self.p, "7", self.user)
        self.assertEqual((m.kind, m.quantity, self.qty()), ("adjustment", D("-3"), D("7")))
        self.assertIsNone(record_stock_count(self.p, "7", self.user))

    def test_untracked_product_rejected(self):
        p = make_product("Service", track_stock=False, package_size="")
        with self.assertRaises(StockError):
            record_stock_in(p, "1", self.user)

    def test_low_stock_alerts(self):
        record_stock_in(self.p, "6", self.user)
        self.assertNotIn(self.p, Product.objects.low_stock())
        record_stock_out(self.p, "1", self.user, reason="sample")
        self.assertIn(self.p, Product.objects.low_stock())
        self.p.refresh_from_db()
        self.assertTrue(self.p.is_low_stock)
        self.p.is_active = False
        self.p.save()
        self.assertNotIn(self.p, Product.objects.low_stock())

    def test_movements_are_append_only(self):
        m = record_stock_in(self.p, "5", self.user)
        m.notes = "edited"
        with self.assertRaises(ImmutableRecordError):
            m.save()
        with self.assertRaises(ImmutableRecordError):
            m.delete()

    def test_database_enforces_direction(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockMovement.objects.create(product=self.p, kind="stock_in", quantity=D("-1"),
                                         balance_after=0, created_by=self.user)

    def test_ledger_check_detects_tampering(self):
        record_stock_in(self.p, "5", self.user)
        call_command("check_stock")  # passes
        Product.objects.filter(pk=self.p.pk).update(stock_qty=D("99"))
        self.assertEqual(len(find_stock_discrepancies()), 1)
        with self.assertRaises(CommandError):
            call_command("check_stock")


class StaleProductSaveTests(TestCase):
    def test_saving_stale_product_never_overwrites_stock(self):
        user = make_user()
        configure_business()
        p = make_product()
        stale = Product.objects.get(pk=p.pk)  # loaded while stock is 0
        record_stock_in(p, "40", user)
        stale.name = "Renamed"
        stale.save()  # e.g. a product edit form submitted from an old page
        p.refresh_from_db()
        self.assertEqual((p.stock_qty, p.name), (D("40"), "Renamed"))
        self.assertEqual(find_stock_discrepancies(), [])

    def test_new_product_starts_at_zero_stock(self):
        p = Product.objects.create(name="X", stock_unit=unit("piece"), stock_qty=D("50"))
        p.refresh_from_db()
        self.assertEqual(p.stock_qty, D("0"))
