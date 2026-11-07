"""Amending issued invoices: same number, history kept, stock adjusted exactly."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.models import AuditLog, ImmutableRecordError
from core.testing import configure_business, make_customer, make_option, make_product, make_staff, make_user
from inventory.models import StockMovement
from inventory.services import InsufficientStockError, find_stock_discrepancies, record_stock_in
from invoices import services
from invoices.models import Invoice, InvoiceLine, InvoiceRevision
from invoices.pdf import render_invoice_pdf
from payments.models import Payment, PaymentAllocation, PaymentMethod


class AmendTestBase(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business()
        self.customer = make_customer("Test Customer")
        self.soap = make_product("Liquid soap", stock_unit="ml")
        self.container = make_option(self.soap, pack="container", content="5000", price="500",
                                     label="5 L container", default=True)
        self.per_litre = make_option(self.soap, per="l", price="120")
        self.bar = make_product("Bar soap", "80.00", package_size="", unit="bar")
        record_stock_in(self.soap, "50000", self.user)  # 50 L
        record_stock_in(self.bar, "100", self.user)
        inv = services.create_draft(user=self.user, customer=self.customer)
        services.add_line(inv, option=self.container, quantity="2")       # 10 L, KES 1,000
        self.invoice = services.issue_invoice(inv, self.user)

    def stock(self, product):
        product.refresh_from_db()
        return product.stock_qty

    @staticmethod
    def header(inv: Invoice, **changes) -> dict:
        data = {"customer": inv.customer, "issue_date": inv.issue_date, "due_date": inv.due_date,
                "payment_terms": inv.payment_terms, "notes": inv.notes, "invoice_discount": inv.invoice_discount}
        data.update(changes)
        return data


class AmendServiceTests(AmendTestBase):
    def amend(self, lines, reason="Added omitted items", **header):
        return services.amend_invoice(self.invoice, user=self.user, header=self.header(self.invoice, **header),
                                      lines=lines, reason=reason)

    def test_adding_an_omitted_item(self):
        number = self.invoice.number
        inv = self.amend([
            {"selling_option": self.container, "quantity": D("2")},
            {"product": self.bar, "quantity": D("5")},                               # left off the first time
            {"selling_option": self.per_litre, "quantity": D("1500"), "entered_unit": "ml"},
        ])
        self.assertEqual((inv.number, inv.status, inv.revision), (number, "issued", 2))
        self.assertEqual(inv.total, D("1580.00"))  # 1,000 + 400 + 180
        self.assertEqual(self.stock(self.soap), D("38500"))  # 50 L − 10 L − 1.5 L
        self.assertEqual(self.stock(self.bar), D("95"))
        # The previous version is archived with its lines and the reason.
        rev = InvoiceRevision.objects.get(invoice=inv)
        self.assertEqual((rev.revision_no, rev.reason, rev.snapshot["total"]), (1, "Added omitted items", "1000.00"))
        self.assertEqual(len(rev.snapshot["lines"]), 1)
        # Old lines stay in the database (stock history points at them) but aren't shown.
        self.assertEqual(inv.lines.count(), 3)
        self.assertEqual(InvoiceLine.all_objects.filter(invoice=inv).count(), 4)
        self.assertEqual(find_stock_discrepancies(), [])
        self.assertTrue(AuditLog.objects.filter(action="invoice.amended", object_id=str(inv.pk)).exists())

    def test_reducing_a_quantity_returns_the_difference(self):
        self.amend([{"selling_option": self.container, "quantity": D("1")}], reason="Only one delivered")
        self.assertEqual(self.stock(self.soap), D("45000"))
        kinds = list(StockMovement.objects.filter(product=self.soap).order_by("pk").values_list("kind", "quantity"))
        self.assertEqual(kinds, [("stock_in", D("50000")), ("sale", D("-10000")), ("sale_reversal", D("10000")),
                                 ("sale", D("-5000"))])

    def test_amending_twice_and_then_cancelling_restores_everything(self):
        self.amend([{"selling_option": self.container, "quantity": D("3")}])
        self.amend([{"selling_option": self.container, "quantity": D("4")}], reason="One more container")
        self.invoice.refresh_from_db()
        self.assertEqual((self.invoice.revision, self.stock(self.soap)), (3, D("30000")))
        services.cancel_invoice(self.invoice, self.user, "Order cancelled")
        self.assertEqual(self.stock(self.soap), D("50000"))
        self.assertEqual(find_stock_discrepancies(), [])

    def test_failed_amendment_changes_nothing(self):
        with self.assertRaises(InsufficientStockError):
            self.amend([{"selling_option": self.container, "quantity": D("20")}])  # 100 L, only 50 L
        self.invoice.refresh_from_db()
        self.assertEqual((self.invoice.revision, self.invoice.total), (1, D("1000.00")))
        self.assertFalse(InvoiceRevision.objects.exists())
        self.assertEqual(self.invoice.lines.get().quantity, D("2"))
        self.assertEqual(self.stock(self.soap), D("40000"))

    def test_the_stock_already_on_the_invoice_counts_as_available(self):
        record_stock_in(self.soap, "0.5", self.user)
        # 40,000.5 ml in the store + 10,000 ml on this invoice → 10 containers fit.
        self.amend([{"selling_option": self.container, "quantity": D("10")}])
        self.assertEqual(self.stock(self.soap), D("0.5"))

    def test_total_cannot_drop_below_what_has_been_paid(self):
        payment = Payment.objects.create(payer_name="Test Customer", amount=D("800"), created_by=self.user,
                                         method=PaymentMethod.objects.get(name="Cash"))
        PaymentAllocation.objects.create(payment=payment, invoice=self.invoice, amount=D("800"))
        with self.assertRaises(services.InvoiceStateError):
            self.amend([{"selling_option": self.container, "quantity": D("1")}])
        self.amend([{"selling_option": self.container, "quantity": D("3")}])  # going up is fine

    def test_rules(self):
        with self.assertRaises(ValidationError):
            self.amend([{"selling_option": self.container, "quantity": D("1")}], reason="  ")
        with self.assertRaises(ValidationError):
            self.amend([])
        draft = services.create_draft(user=self.user, customer=self.customer)
        with self.assertRaises(services.InvoiceStateError):
            services.amend_invoice(draft, user=self.user, header=self.header(draft), reason="x",
                                   lines=[{"selling_option": self.container, "quantity": D("1")}])
        services.cancel_invoice(self.invoice, self.user, "x")
        with self.assertRaises(services.InvoiceStateError):
            self.amend([{"selling_option": self.container, "quantity": D("1")}])

    def test_revisions_and_issued_lines_are_still_protected(self):
        self.amend([{"selling_option": self.container, "quantity": D("3")}])
        rev = InvoiceRevision.objects.get()
        rev.reason = "changed"
        with self.assertRaises(ImmutableRecordError):
            rev.save()
        with self.assertRaises(ImmutableRecordError):
            rev.delete()
        line = self.invoice.lines.get()
        line.quantity = D("99")
        with self.assertRaises(ImmutableRecordError):
            line.save()
        self.invoice.refresh_from_db()
        self.invoice.total = D("1")
        with self.assertRaises(ImmutableRecordError):
            self.invoice.save()

    def test_superseded_version_prints_from_its_snapshot(self):
        self.amend([{"selling_option": self.container, "quantity": D("3")}])
        old, lines = InvoiceRevision.objects.get().as_invoice()
        self.assertEqual((old.total, old.revision, lines[0].quantity_display), (D("1000.00"), 1, "2 containers"))
        raw = render_invoice_pdf(old, lines=lines, superseded_on=old.issue_date, compress=False).read().decode("latin-1")
        self.assertIn("SUPERSEDED", raw)
        self.assertIn("1,000.00", raw)
        current = render_invoice_pdf(Invoice.objects.get(pk=self.invoice.pk), compress=False).read().decode("latin-1")
        self.assertIn("rev. 2", current)


class AmendViewTests(AmendTestBase):
    def post_data(self, lines, reason="Added the bar soap that was left off"):
        data = {
            "customer": self.customer.pk, "customer_name": "", "issue_date": str(self.invoice.issue_date),
            "due_date": "", "invoice_discount": "", "payment_terms": "", "notes": "", "reason": reason,
            "lines-TOTAL_FORMS": str(len(lines)), "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "200",
        }
        for i, line in enumerate(lines):
            data.update({f"lines-{i}-{k}": v for k, v in line.items()})
        return data

    def test_amend_screen_and_save(self):
        self.client.force_login(self.user)
        url = reverse("invoices:amend", args=[self.invoice.pk])
        r = self.client.get(url)
        self.assertContains(r, f"Amend invoice {self.invoice.number}")
        self.assertEqual(r.context["lines"].initial[0]["selling_option"], self.container.pk)
        # Stock already on this invoice is shown as available (40 L in store + 10 L on it).
        soap = next(p for p in r.context["editor_data"]["products"] if p["id"] == self.soap.pk)
        self.assertEqual(soap["stockDisplay"], "50 L")

        r = self.client.post(url, self.post_data([
            {"product": self.soap.pk, "selling_option": self.container.pk, "quantity": "2"},
            {"product": self.bar.pk, "quantity": "3"},
        ], reason=""))
        self.assertEqual(r.status_code, 200)  # reason required
        self.assertEqual(self.invoice.revisions.count(), 0)

        r = self.client.post(url, self.post_data([
            {"product": self.soap.pk, "selling_option": self.container.pk, "quantity": "2"},
            {"product": self.bar.pk, "quantity": "3"},
        ]))
        self.assertRedirects(r, reverse("invoices:detail", args=[self.invoice.pk]))
        self.invoice.refresh_from_db()
        self.assertEqual((self.invoice.revision, self.invoice.total), (2, D("1240.00")))
        self.assertEqual(self.stock(self.bar), D("97"))

        detail = self.client.get(reverse("invoices:detail", args=[self.invoice.pk]))
        self.assertContains(detail, "Added the bar soap that was left off")
        self.assertContains(detail, "rev. 2")
        rev_url = reverse("invoices:revision", args=[self.invoice.pk, 1])
        self.assertContains(detail, rev_url)
        old = self.client.get(rev_url)
        self.assertContains(old, "Superseded")
        self.assertContains(old, "2 containers")
        image = self.client.get(reverse("invoices:revision_image", args=[self.invoice.pk, 1]))
        self.assertEqual(image["Content-Type"], "image/png")
        self.assertEqual(self.client.get(reverse("invoices:revision", args=[self.invoice.pk, 9])).status_code, 404)

    def test_not_enough_stock_shows_a_message(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse("invoices:amend", args=[self.invoice.pk]), self.post_data([
            {"product": self.soap.pk, "selling_option": self.container.pk, "quantity": "30"},
        ]))
        self.assertContains(r, "Not enough stock")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.revision, 1)

    def test_permission_needed(self):
        staff = make_staff(perms=("invoices.view_invoice", "invoices.change_invoice"))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("invoices:amend", args=[self.invoice.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("invoices:detail", args=[self.invoice.pk])), "Amend")
