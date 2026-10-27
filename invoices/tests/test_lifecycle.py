import datetime
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import AuditLog, ImmutableRecordError
from core.testing import configure_business, make_customer, make_product, make_user
from inventory.models import StockMovement
from inventory.services import InsufficientStockError, find_stock_discrepancies, record_stock_in
from invoices import services
from invoices.models import Invoice, InvoiceLine, PaymentState
from invoices.numbering import allocate_invoice_number
from payments.models import Payment, PaymentAllocation, PaymentMethod


class InvoiceTestBase(TestCase):
    def setUp(self):
        self.user = make_user()
        self.settings = configure_business()
        self.soap = make_product("Bar Soap", "80.00", package_size="200 g", unit="piece")
        self.liquid = make_product("Liquid Soap", "650.00", package_size="5 L", unit="container")
        record_stock_in(self.soap, "100", self.user, reference="Opening stock")
        record_stock_in(self.liquid, "10", self.user, reference="Opening stock")
        self.customer = make_customer("Test School", kind="organization", phone="0711 111111")

    def draft(self, **kw):
        inv = services.create_draft(user=self.user, customer=self.customer, **kw)
        services.add_line(inv, product=self.soap, quantity="10")
        services.add_line(inv, product=self.liquid, quantity="2", unit_price="600.00")
        return inv

    def stock(self, product):
        product.refresh_from_db()
        return product.stock_qty


class DraftTests(InvoiceTestBase):
    def test_draft_copies_customer_and_prices(self):
        inv = self.draft()
        self.assertEqual(inv.customer_name, "Test School")
        lines = list(inv.lines.all())
        self.assertEqual(lines[0].unit_price, D("80.00"))
        self.assertEqual(lines[0].description, "Bar Soap")
        self.assertEqual(lines[1].catalogue_price, D("650.00"))
        self.assertTrue(lines[1].is_price_overridden)
        inv.refresh_from_db()
        self.assertEqual(inv.total, D("2000.00"))  # 10×80 + 2×600

    def test_price_override_does_not_change_catalogue(self):
        self.draft()
        self.liquid.refresh_from_db()
        self.assertEqual(self.liquid.default_price, D("650.00"))

    def test_one_time_customer(self):
        inv = services.create_draft(user=self.user, customer_name="Walk-in buyer")
        self.assertIsNone(inv.customer)
        self.assertEqual(inv.customer_name, "Walk-in buyer")

    def test_customer_name_required(self):
        with self.assertRaises(ValidationError):
            services.create_draft(user=self.user, customer_name="  ")

    def test_drafts_do_not_touch_stock(self):
        self.draft()
        self.assertEqual(self.stock(self.soap), D("100"))

    def test_due_date_from_settings(self):
        self.settings.default_payment_terms_days = 14
        self.settings.save()
        inv = services.create_draft(user=self.user, customer_name="X", issue_date=datetime.date(2026, 3, 1))
        self.assertEqual(inv.due_date, datetime.date(2026, 3, 15))


class IssueTests(InvoiceTestBase):
    def test_issue_assigns_number_snapshots_and_deducts_stock(self):
        inv = services.issue_invoice(self.draft(issue_date=datetime.date(2026, 5, 4)), self.user)
        self.assertEqual(inv.status, Invoice.Status.ISSUED)
        self.assertEqual(inv.number, "INV-2026-0001")
        self.assertEqual(inv.business_snapshot["name"], "Test Business")
        self.assertEqual(self.stock(self.soap), D("90"))
        self.assertEqual(self.stock(self.liquid), D("8"))
        self.assertEqual(StockMovement.objects.filter(kind="sale").count(), 2)
        self.assertTrue(AuditLog.objects.filter(action="invoice.issued").exists())
        self.assertEqual(find_stock_discrepancies(), [])

    def test_numbers_are_sequential_and_unique(self):
        numbers = [services.issue_invoice(self.draft(issue_date=datetime.date(2026, 1, 2)), self.user).number
                   for _ in range(3)]
        self.assertEqual(numbers, ["INV-2026-0001", "INV-2026-0002", "INV-2026-0003"])

    def test_numbering_resets_each_year(self):
        a = services.issue_invoice(self.draft(issue_date=datetime.date(2026, 12, 31)), self.user)
        b = services.issue_invoice(self.draft(issue_date=datetime.date(2027, 1, 1)), self.user)
        self.assertEqual((a.number, b.number), ("INV-2026-0001", "INV-2027-0001"))

    def test_numbering_skips_numbers_already_used(self):
        inv = services.issue_invoice(self.draft(issue_date=datetime.date(2026, 1, 2)), self.user)
        # Simulate the counter being behind (e.g. restored from an older backup).
        from invoices.models import InvoiceSequence
        InvoiceSequence.objects.update(last_number=0)
        nxt = allocate_invoice_number(prefix="INV", padding=4, reset_yearly=True, on_date=datetime.date(2026, 2, 1))
        self.assertNotEqual(nxt, inv.number)
        self.assertEqual(nxt, "INV-2026-0002")

    def test_duplicate_number_rejected_by_database(self):
        a = services.issue_invoice(self.draft(), self.user)
        b = self.draft()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.filter(pk=b.pk).update(number=a.number)

    def test_cannot_issue_twice_or_deduct_twice(self):
        inv = services.issue_invoice(self.draft(), self.user)
        with self.assertRaises(services.InvoiceStateError):
            services.issue_invoice(inv, self.user)
        self.assertEqual(self.stock(self.soap), D("90"))

    def test_insufficient_stock_blocks_issue_and_burns_no_number(self):
        inv = services.create_draft(user=self.user, customer_name="Big order")
        services.add_line(inv, product=self.liquid, quantity="11")
        with self.assertRaises(InsufficientStockError):
            services.issue_invoice(inv, self.user)
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.DRAFT)
        self.assertIsNone(inv.number)
        self.assertEqual(self.stock(self.liquid), D("10"))
        ok = services.issue_invoice(self.draft(), self.user)
        self.assertTrue(ok.number.endswith("0001"))

    def test_shortage_counts_the_same_product_across_lines(self):
        inv = services.create_draft(user=self.user, customer_name="Split order")
        services.add_line(inv, product=self.liquid, quantity="6")
        services.add_line(inv, product=self.liquid, quantity="6")
        with self.assertRaises(InsufficientStockError):
            services.issue_invoice(inv, self.user)

    def test_negative_stock_allowed_when_setting_on(self):
        self.settings.allow_negative_stock = True
        self.settings.save()
        inv = services.create_draft(user=self.user, customer_name="Big order")
        services.add_line(inv, product=self.liquid, quantity="12")
        services.issue_invoice(inv, self.user)
        self.assertEqual(self.stock(self.liquid), D("-2"))

    def test_untracked_products_and_free_text_lines_skip_stock(self):
        service_item = make_product("Delivery", "300.00", package_size="", unit="trip", track_stock=False)
        inv = services.create_draft(user=self.user, customer_name="X")
        services.add_line(inv, product=service_item, quantity="1")
        services.add_line(inv, description="Custom label printing", quantity="1", unit_price="150")
        services.issue_invoice(inv, self.user)
        self.assertFalse(StockMovement.objects.filter(kind="sale").exists())

    def test_requires_business_settings(self):
        self.settings.name = ""
        self.settings.save()
        with self.assertRaises(services.InvoiceStateError):
            services.issue_invoice(self.draft(), self.user)

    def test_requires_lines(self):
        inv = services.create_draft(user=self.user, customer_name="Empty")
        with self.assertRaises(services.InvoiceStateError):
            services.issue_invoice(inv, self.user)


class ImmutabilityTests(InvoiceTestBase):
    def test_issued_invoice_financials_frozen(self):
        inv = services.issue_invoice(self.draft(), self.user)
        inv.total = D("1.00")
        with self.assertRaises(ImmutableRecordError):
            inv.save()

    def test_issued_lines_frozen(self):
        inv = services.issue_invoice(self.draft(), self.user)
        line = inv.lines.first()
        line.unit_price = D("1.00")
        with self.assertRaises(ImmutableRecordError):
            line.save()
        with self.assertRaises(ImmutableRecordError):
            line.delete()
        with self.assertRaises(services.InvoiceStateError):
            services.add_line(inv, product=self.soap, quantity="1")

    def test_issued_invoice_cannot_be_deleted(self):
        inv = services.issue_invoice(self.draft(), self.user)
        with self.assertRaises(ImmutableRecordError):
            inv.delete()

    def test_draft_can_be_deleted(self):
        inv = self.draft()
        inv.delete()
        self.assertFalse(InvoiceLine.objects.exists())

    def test_catalogue_changes_do_not_alter_issued_invoice(self):
        inv = services.issue_invoice(self.draft(), self.user)
        self.soap.name = "Renamed soap"
        self.soap.default_price = D("999")
        self.soap.is_active = False  # archived
        self.soap.save()
        line = inv.lines.get(product=self.soap)
        self.assertEqual((line.description, line.unit_price), ("Bar Soap", D("80.00")))
        self.assertEqual(line.product, self.soap)  # archived product still linked

    def test_business_settings_changes_do_not_alter_issued_invoice(self):
        inv = services.issue_invoice(self.draft(), self.user)
        self.settings.name = "New Name Ltd"
        self.settings.save()
        inv.refresh_from_db()
        self.assertEqual(inv.business_snapshot["name"], "Test Business")

    def test_etims_reference_can_be_added_after_issue(self):
        inv = services.issue_invoice(self.draft(), self.user)
        inv.etims_reference = "ETIMS-REF-1"
        inv.save()


class CancellationAndCorrectionTests(InvoiceTestBase):
    def test_cancel_restores_stock_once(self):
        inv = services.issue_invoice(self.draft(), self.user)
        services.cancel_invoice(inv, self.user, "Customer order changed")
        self.assertEqual(self.stock(self.soap), D("100"))
        self.assertEqual(self.stock(self.liquid), D("10"))
        self.assertEqual(StockMovement.objects.filter(kind="sale_reversal").count(), 2)
        with self.assertRaises(services.InvoiceStateError):
            services.cancel_invoice(inv, self.user, "again")
        self.assertEqual(self.stock(self.soap), D("100"))
        self.assertEqual(find_stock_discrepancies(), [])

    def test_restore_called_twice_does_nothing_more(self):
        from inventory.services import restore_stock_for_invoice
        inv = services.issue_invoice(self.draft(), self.user)
        services.cancel_invoice(inv, self.user, "Wrong customer")
        self.assertEqual(restore_stock_for_invoice(inv, self.user), [])
        self.assertEqual(self.stock(self.soap), D("100"))

    def test_database_blocks_duplicate_sale_movement(self):
        inv = services.issue_invoice(self.draft(), self.user)
        line = inv.lines.first()
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockMovement.objects.create(product=line.product, kind="sale", quantity=-line.quantity,
                                         balance_after=0, invoice_line=line, created_by=self.user)

    def test_cancel_requires_reason(self):
        inv = services.issue_invoice(self.draft(), self.user)
        with self.assertRaises(ValidationError):
            services.cancel_invoice(inv, self.user, "  ")

    def test_cannot_cancel_draft(self):
        with self.assertRaises(services.InvoiceStateError):
            services.cancel_invoice(self.draft(), self.user, "x")

    def test_cancel_blocked_while_payments_exist(self):
        inv = services.issue_invoice(self.draft(), self.user)
        pay = Payment.objects.create(payer_name="Test School", amount=D("500"), method=PaymentMethod.objects.get(name="Cash"),
                                     created_by=self.user)
        PaymentAllocation.objects.create(payment=pay, invoice=inv, amount=D("500"))
        with self.assertRaises(services.InvoiceStateError):
            services.cancel_invoice(inv, self.user, "x")
        self.assertEqual(self.stock(self.soap), D("90"))

    def test_correction_cancel_and_reissue_nets_stock_correctly(self):
        original = services.issue_invoice(self.draft(), self.user)            # soap 100→90, liquid 10→8
        services.cancel_invoice(original, self.user, "Wrong quantity")       # back to 100 / 10
        replacement = services.create_replacement_draft(original, self.user)
        self.assertEqual(replacement.replaces, original)
        self.assertEqual(replacement.lines.count(), 2)
        line = replacement.lines.get(product=self.soap)
        line.quantity = D("4")
        line.save()
        services.recalculate(replacement)
        services.issue_invoice(replacement, self.user)                        # soap 100→96, liquid 10→8
        self.assertEqual(self.stock(self.soap), D("96"))
        self.assertEqual(self.stock(self.liquid), D("8"))
        replacement.refresh_from_db()
        self.assertEqual(replacement.total, D("1520.00"))  # 4×80 + 2×600
        self.assertEqual(find_stock_discrepancies(), [])
        with self.assertRaises(services.InvoiceStateError):
            services.create_replacement_draft(original, self.user)

    def test_reversal_matches_original_even_if_tracking_switched_off(self):
        inv = services.issue_invoice(self.draft(), self.user)
        self.soap.track_stock = False
        self.soap.save()
        services.cancel_invoice(inv, self.user, "Returned")
        self.assertEqual(self.stock(self.soap), D("100"))


class PaymentStateTests(InvoiceTestBase):
    def pay(self, inv, amount, reversed_=False, ref=""):
        p = Payment.objects.create(payer_name="Test School", amount=D(amount), reference=ref,
                                   method=PaymentMethod.objects.get(name="M-Pesa"), created_by=self.user)
        PaymentAllocation.objects.create(payment=p, invoice=inv, amount=D(amount))
        return p

    def test_states(self):
        inv = services.issue_invoice(self.draft(issue_date=datetime.date(2026, 1, 1), due_date=datetime.date(2026, 1, 31)), self.user)
        self.assertEqual(inv.payment_state(today=datetime.date(2026, 1, 10)), PaymentState.UNPAID)
        self.pay(inv, "500", ref="AAA111")
        self.assertEqual(inv.payment_state(today=datetime.date(2026, 1, 10)), PaymentState.PARTIAL)
        self.assertEqual(inv.payment_state(today=datetime.date(2026, 2, 1)), PaymentState.OVERDUE)
        self.assertEqual(inv.get_balance(), D("1500.00"))
        self.pay(inv, "1500", ref="BBB222")
        self.assertEqual(inv.payment_state(today=datetime.date(2026, 2, 1)), PaymentState.PAID)
        self.assertEqual(inv.get_balance(), D("0.00"))

    def test_reversed_payments_excluded_from_balance(self):
        inv = services.issue_invoice(self.draft(), self.user)
        p = self.pay(inv, "2000", ref="CCC333")
        from django.utils import timezone
        p.is_reversed, p.reversed_at, p.reversal_reason, p.reversed_by = True, timezone.now(), "Entered twice", self.user
        p.save()
        annotated = Invoice.objects.with_payment_totals().get(pk=inv.pk)
        self.assertEqual((annotated.amount_paid, annotated.balance), (D("0.00"), D("2000.00")))

    def test_overdue_queryset(self):
        inv = services.issue_invoice(self.draft(issue_date=datetime.date(2026, 1, 1), due_date=datetime.date(2026, 1, 31)), self.user)
        self.assertIn(inv, Invoice.objects.overdue(today=datetime.date(2026, 2, 1)))
        self.assertNotIn(inv, Invoice.objects.overdue(today=datetime.date(2026, 1, 31)))
