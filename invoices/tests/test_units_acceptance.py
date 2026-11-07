"""
Acceptance tests for unit-aware quantities, pricing and stock (the 12 cases
in the feature requirement). Products and prices here are test data only.
"""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.testing import configure_business, make_customer, make_option, make_product, make_user, unit
from inventory.models import StockMovement
from inventory.services import (
    StockError, deduct_stock_for_invoice, find_stock_discrepancies, record_return, record_stock_in,
)
from invoices import services
from invoices.calculations import calculate_line
from products.pricing import resolve_quantity
from products.units import IncompatibleUnits, check_compatible, convert, format_in_display_unit


class UnitConversionTests(TestCase):
    def test_01_one_litre_is_1000_ml(self):
        self.assertEqual(convert("1", unit("l"), unit("ml")), D("1000"))
        self.assertEqual(convert("1500", unit("ml"), unit("l")), D("1.5"))

    def test_02_one_kilogram_is_1000_g(self):
        self.assertEqual(convert("1", unit("kg"), unit("g")), D("1000"))
        self.assertEqual(convert("250", unit("g"), unit("kg")), D("0.25"))

    def test_07_incompatible_units_are_rejected(self):
        for a, b in [("l", "kg"), ("ml", "g"), ("kg", "bottle"), ("bottle", "carton")]:
            with self.subTest(a=a, b=b), self.assertRaises(IncompatibleUnits):
                check_compatible(unit(a), unit(b))
        product = make_product("Liquid soap", stock_unit="ml")
        per_litre = make_option(product, per="l", price="250", default=True)
        with self.assertRaises(IncompatibleUnits):
            resolve_quantity(per_litre, "2", unit("kg"))


class UnitAwareInvoiceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business()
        self.customer = make_customer("Test Customer")
        # One liquid product kept in ml, sold three ways.
        self.soap = make_product("Liquid soap", stock_unit="ml")
        self.per_litre = make_option(self.soap, per="l", price="250", label="loose, per litre", default=True)
        self.litre_bottle = make_option(self.soap, pack="bottle", content="1000", price="250", label="1 L bottle")
        self.small_bottle = make_option(self.soap, pack="bottle", content="500", price="140", label="500 ml bottle")
        record_stock_in(self.soap, "20000", self.user, entered=("20", "L"))  # 20 L

    def draft(self):
        return services.create_draft(user=self.user, customer=self.customer)

    def stock(self):
        self.soap.refresh_from_db()
        return self.soap.stock_qty

    def test_03_1500_ml_at_250_per_litre_is_375(self):
        line = services.add_line(self.draft(), option=self.per_litre, quantity="1500", unit="ml")
        self.assertEqual(line.line_total, D("375.00"))
        self.assertEqual((line.price_quantity, line.base_quantity, line.conversion_factor),
                         (D("1.5"), D("1500"), D("0.001")))
        self.assertEqual((line.quantity_display, line.price_display_unit), ("1,500 ml", "per L"))
        # The same amount typed in litres gives the same result.
        same = services.add_line(self.draft(), option=self.per_litre, quantity="1.5", unit="l")
        self.assertEqual((same.line_total, same.base_quantity), (D("375.00"), D("1500")))

    def test_04_five_1_litre_bottles_at_250_is_1250(self):
        line = services.add_line(self.draft(), option=self.litre_bottle, quantity="5")
        self.assertEqual((line.line_total, line.base_quantity), (D("1250.00"), D("5000")))
        self.assertEqual(line.quantity_display, "5 bottles")

    def test_05_five_500_ml_bottles_at_140_is_700(self):
        line = services.add_line(self.draft(), option=self.small_bottle, quantity="5")
        self.assertEqual((line.line_total, line.base_quantity), (D("700.00"), D("2500")))

    def test_06_three_500_ml_bottles_from_20_litres_leaves_18_5_litres(self):
        inv = self.draft()
        services.add_line(inv, option=self.small_bottle, quantity="3")
        services.issue_invoice(inv, self.user)
        self.assertEqual(self.stock(), D("18500"))
        self.assertEqual(format_in_display_unit(self.stock(), self.soap.stock_unit), "18.5 L")
        sale = StockMovement.objects.get(kind="sale")
        self.assertEqual((sale.quantity, sale.entered_quantity, sale.entered_unit), (D("-1500"), D("3"), "bottles"))

    def test_08_package_pricing_is_not_pro_rated(self):
        # Two 500 ml bottles hold 1 L but cost 2 × 140, not the per-litre price.
        line = services.add_line(self.draft(), option=self.small_bottle, quantity="2")
        self.assertEqual(line.line_total, D("280.00"))
        # A pack is counted in packs: typing "ml" against it is refused.
        with self.assertRaises(IncompatibleUnits):
            services.add_line(self.draft(), option=self.small_bottle, quantity="250", unit="ml")
        # Half a bottle is half the pack price only because the pack price is per bottle (no hidden per-ml maths).
        half = services.add_line(self.draft(), option=self.small_bottle, quantity="0.5")
        self.assertEqual((half.line_total, half.base_quantity), (D("70.00"), D("250")))

    def test_09_no_double_deduction(self):
        inv = self.draft()
        services.add_line(inv, option=self.litre_bottle, quantity="2")
        inv = services.issue_invoice(inv, self.user)
        self.assertEqual(self.stock(), D("18000"))
        with self.assertRaises(StockError):
            deduct_stock_for_invoice(inv, self.user)
        with self.assertRaises(services.InvoiceStateError):
            services.issue_invoice(inv, self.user)
        line = inv.lines.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockMovement.objects.create(product=self.soap, kind="sale", quantity=D("-1"), balance_after=0,
                                         invoice_line=line, created_by=self.user)
        self.assertEqual(self.stock(), D("18000"))
        self.assertEqual(find_stock_discrepancies(), [])

    def test_10_issued_invoices_keep_their_snapshot(self):
        inv = self.draft()
        services.add_line(inv, option=self.per_litre, quantity="1500", unit="ml")
        services.add_line(inv, option=self.small_bottle, quantity="3")
        inv = services.issue_invoice(inv, self.user)
        # Later catalogue changes: new prices, new pack size, renamed.
        self.per_litre.price = D("300")
        self.per_litre.save()
        self.small_bottle.price, self.small_bottle.content_quantity, self.small_bottle.label = D("150"), D("750"), "750 ml"
        self.small_bottle.save()
        self.soap.name = "Renamed soap"
        self.soap.save()
        loose, bottles = inv.lines.order_by("position")
        self.assertEqual((loose.unit_price, loose.price_unit, loose.price_quantity, loose.line_total),
                         (D("250.00"), "L", D("1.5"), D("375.00")))
        self.assertEqual((bottles.package_size, bottles.unit_price, bottles.base_quantity, bottles.line_total),
                         ("500 ml bottle", D("140.00"), D("1500"), D("420.00")))
        inv.refresh_from_db()
        self.assertEqual(inv.total, D("795.00"))
        # Cancelling returns exactly what was taken, not what the new pack size would say.
        services.cancel_invoice(inv, self.user, "Test")
        self.assertEqual(self.stock(), D("20000"))

    def test_11_decimal_fractional_quantities_and_money(self):
        cases = [
            ("1500", "ml", "250.00", D("375.00")),
            ("333", "ml", "250.00", D("83.25")),
            ("0.75", "l", "333.33", D("250.00")),     # 249.9975 → 250.00 (half up)
            ("1.25", "l", "99.99", D("124.99")),      # 124.9875 → 124.99
            ("2.5", "l", "0.10", D("0.25")),
        ]
        for qty, code, price, expected in cases:
            with self.subTest(qty=qty, unit=code, price=price):
                line = services.add_line(self.draft(), option=self.per_litre, quantity=qty, unit=code, unit_price=price)
                self.assertEqual(line.line_total, expected)
                self.assertIsInstance(line.line_total, D)
        powder = make_product("Washing powder", stock_unit="g")
        per_kg = make_option(powder, per="kg", price="120.50", default=True)
        line = services.add_line(self.draft(), option=per_kg, quantity="2.25", unit="kg")
        self.assertEqual((line.line_total, line.base_quantity), (D("271.13"), D("2250")))  # 271.125 → 271.13
        self.assertEqual(calculate_line(D("0.1") + D("0.2"), "10").total, D("3.00"))  # no float drift

    def test_12_returns_and_cancellations_keep_inventory_accurate(self):
        inv = self.draft()
        services.add_line(inv, option=self.small_bottle, quantity="4")  # 2 L
        services.add_line(inv, option=self.per_litre, quantity="3", unit="l")  # 3 L
        inv = services.issue_invoice(inv, self.user)
        self.assertEqual(self.stock(), D("15000"))
        # A customer brings back one unopened 500 ml bottle.
        record_return(self.soap, D("500"), self.user, reference=inv.number, entered=(D("1"), "500 ml bottle"))
        self.assertEqual(self.stock(), D("15500"))
        # Cancelling returns exactly what the invoice took (5 L), once.
        services.cancel_invoice(inv, self.user, "Order cancelled")
        self.assertEqual(self.stock(), D("20500"))
        self.assertEqual(StockMovement.objects.filter(kind="sale_reversal").count(), 2)
        self.assertEqual(find_stock_discrepancies(), [])

    def test_editor_posts_units_and_options(self):
        """The invoice screen sends the selling option and the unit typed."""
        self.client.force_login(self.user)
        data = {
            "customer": self.customer.pk, "customer_name": "", "issue_date": "2026-10-27",
            "lines-TOTAL_FORMS": "3", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "200",
            "lines-0-product": self.soap.pk, "lines-0-selling_option": self.per_litre.pk,
            "lines-0-quantity": "1500", "lines-0-entered_unit": "ml",
            "lines-1-product": self.soap.pk, "lines-1-selling_option": self.small_bottle.pk, "lines-1-quantity": "5",
            "lines-2-description": "Delivery", "lines-2-quantity": "1", "lines-2-free_unit": "trip",
            "lines-2-unit_price": "200",
        }
        r = self.client.post(reverse("invoices:create"), data)
        inv = self.customer.invoices.get()
        self.assertRedirects(r, reverse("invoices:detail", args=[inv.pk]))
        self.assertEqual([l.line_total for l in inv.lines.all()], [D("375.00"), D("700.00"), D("200.00")])
        self.assertEqual(inv.total, D("1275.00"))
        detail = self.client.get(reverse("invoices:detail", args=[inv.pk]))
        self.assertContains(detail, "1,500 ml")
        self.assertContains(detail, "per L")
        # A unit of the wrong kind is shown as an error on that line, nothing saved.
        bad = {**data, "lines-0-entered_unit": "kg"}
        r = self.client.post(reverse("invoices:create"), bad)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "can&#x27;t be converted")
        self.assertEqual(self.customer.invoices.count(), 1)

    def test_selling_option_must_belong_to_product(self):
        other = make_product("Bleach")
        with self.assertRaises(ValidationError):
            services.add_line(self.draft(), product=other, option=self.small_bottle, quantity="1")
