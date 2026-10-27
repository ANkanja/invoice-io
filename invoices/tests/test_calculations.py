from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from core.money import format_money, quantize, to_decimal
from invoices.calculations import LineInput, calculate_invoice, calculate_line


class MoneyHelperTests(SimpleTestCase):
    def test_rejects_float(self):
        with self.assertRaises(TypeError):
            to_decimal(0.1)

    def test_half_up_rounding(self):
        self.assertEqual(quantize("2.345"), D("2.35"))
        self.assertEqual(quantize("2.344"), D("2.34"))

    def test_format(self):
        self.assertEqual(format_money(D("1234567.5")), "KES 1,234,567.50")
        self.assertEqual(format_money(D("-20")), "KES -20.00")


class LineCalculationTests(SimpleTestCase):
    def test_simple_line(self):
        r = calculate_line("3", "150.00")
        self.assertEqual((r.gross, r.discount, r.total), (D("450.00"), D("0.00"), D("450.00")))

    def test_fractional_quantity_is_exact(self):
        # 0.1 + 0.2 style float errors must not appear
        r = calculate_line("2.5", "33.33")
        self.assertEqual(r.gross, D("83.33"))  # 83.325 rounds half-up

    def test_line_discount(self):
        r = calculate_line("2", "500", "100")
        self.assertEqual(r.total, D("900.00"))

    def test_validation(self):
        for qty, price, disc in [("0", "10", "0"), ("-1", "10", "0"), ("1", "-5", "0"), ("1", "10", "-1"), ("1", "10", "10.01")]:
            with self.subTest(qty=qty, price=price, disc=disc), self.assertRaises(ValidationError):
                calculate_line(qty, price, disc)


class InvoiceCalculationTests(SimpleTestCase):
    lines = [LineInput(D("3"), D("150.00")), LineInput(D("2"), D("1200.00"), D("200.00")), LineInput(D("1"), D("49.99"))]

    def test_multiple_lines_no_tax(self):
        t = calculate_invoice(self.lines)
        self.assertEqual(t.subtotal, D("2699.99"))  # 450 + 2200 + 49.99
        self.assertEqual(t.line_discount_total, D("200.00"))
        self.assertEqual(t.total, D("2699.99"))
        self.assertEqual(t.tax_total, D("0.00"))

    def test_invoice_discount(self):
        t = calculate_invoice(self.lines, invoice_discount="99.99")
        self.assertEqual(t.total, D("2600.00"))

    def test_invoice_discount_cannot_exceed_subtotal(self):
        with self.assertRaises(ValidationError):
            calculate_invoice(self.lines, invoice_discount="3000")

    def test_tax_exclusive(self):
        t = calculate_invoice([LineInput(D("1"), D("1000"))], tax_rate="16")
        self.assertEqual((t.taxable_amount, t.tax_total, t.total), (D("1000.00"), D("160.00"), D("1160.00")))

    def test_tax_inclusive(self):
        t = calculate_invoice([LineInput(D("1"), D("1160"))], tax_rate="16", prices_include_tax=True)
        self.assertEqual((t.taxable_amount, t.tax_total, t.total), (D("1000.00"), D("160.00"), D("1160.00")))

    def test_many_small_lines_sum_exactly(self):
        t = calculate_invoice([LineInput(D("1"), D("0.10"))] * 1000)
        self.assertEqual(t.total, D("100.00"))

    def test_empty_invoice_is_zero(self):
        self.assertEqual(calculate_invoice([]).total, D("0.00"))
