import datetime
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from core.testing import configure_business, make_customer, make_product, make_staff, make_user, set_price
from inventory.models import StockMovement
from inventory.services import find_stock_discrepancies, record_stock_in
from invoices import services
from invoices.models import Invoice
from payments.models import Payment, PaymentAllocation, PaymentMethod


def editor_post(customer=None, lines=(), **header):
    """Build the POST data the editor form sends."""
    data = {
        "customer": customer.pk if customer else "",
        "customer_name": "", "customer_contact_person": "", "customer_phone": "", "customer_email": "",
        "customer_address": "", "customer_tax_pin": "",
        "issue_date": "2026-10-27", "due_date": "", "invoice_discount": "", "payment_terms": "", "notes": "",
        "lines-TOTAL_FORMS": str(len(lines)), "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "200",
    }
    data.update(header)
    for i, line in enumerate(lines):
        row = {"product": "", "selling_option": "", "description": "", "entered_unit": "", "free_unit": "",
               "quantity": "", "unit_price": "", "line_discount": ""}
        row.update({k: (v.pk if hasattr(v, "pk") else v) for k, v in line.items()})
        data.update({f"lines-{i}-{k}": v for k, v in row.items()})
    return data


class EditorTests(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business(default_payment_terms_days=14, default_payment_terms="Pay within 14 days")
        self.client.force_login(self.user)
        self.soap = make_product("Bar soap", "80.00", package_size="200 g", unit="bar")
        self.liquid = make_product("Liquid soap", "650.00", package_size="5 L", unit="container")
        record_stock_in(self.soap, "100", self.user)
        record_stock_in(self.liquid, "10", self.user)
        self.school = make_customer("Test School", kind="organization", phone="0711 000000", billing_address="P.O. Box 1")

    def test_new_form_prefills_from_settings(self):
        r = self.client.get(reverse("invoices:create"))
        initial = r.context["header"].initial
        self.assertEqual(initial["due_date"] - initial["issue_date"], datetime.timedelta(days=14))
        self.assertEqual(initial["payment_terms"], "Pay within 14 days")
        self.assertIn('id="line-template"', r.content.decode())
        r = self.client.get(reverse("invoices:create"), {"customer": self.school.pk})
        self.assertEqual(r.context["header"].initial["customer"], self.school.pk)

    def test_create_draft_with_saved_customer(self):
        data = editor_post(self.school, [
            {"product": self.soap, "quantity": "10", "unit_price": "75"},       # price override
            {"product": self.liquid, "quantity": "2"},                          # blank price → catalogue price
            {"description": "Delivery", "quantity": "1", "unit_price": "300"},  # free text
            {},                                                                 # blank row ignored
        ], invoice_discount="50")
        r = self.client.post(reverse("invoices:create"), data)
        inv = Invoice.objects.get()
        self.assertRedirects(r, reverse("invoices:detail", args=[inv.pk]))
        self.assertEqual((inv.status, inv.customer, inv.customer_phone), ("draft", self.school, "0711 000000"))
        lines = list(inv.lines.all())
        self.assertEqual([l.description for l in lines], ["Bar soap", "Liquid soap", "Delivery"])
        self.assertEqual(lines[0].unit_price, D("75.00"))
        self.assertTrue(lines[0].is_price_overridden)
        self.assertEqual(lines[1].unit_price, D("650.00"))
        self.assertEqual((lines[0].unit, lines[0].package_size), ("bar", "200 g bar"))
        self.assertEqual((inv.subtotal, inv.total), (D("2350.00"), D("2300.00")))  # 750+1300+300 − 50
        self.soap.refresh_from_db()
        self.assertEqual((self.soap.default_price, self.soap.stock_qty), (D("80.00"), D("100")))  # untouched

    def test_one_time_customer(self):
        data = editor_post(None, [{"description": "Soap", "quantity": "1", "unit_price": "50"}],
                           customer_name="  Walk-in   buyer ", customer_phone="0700 111222")
        self.client.post(reverse("invoices:create"), data)
        inv = Invoice.objects.get()
        self.assertIsNone(inv.customer)
        self.assertEqual((inv.customer_name, inv.customer_phone), ("Walk-in buyer", "0700 111222"))

    def test_validation_errors(self):
        url = reverse("invoices:create")
        cases = [
            (editor_post(None, [{"description": "X", "quantity": "1", "unit_price": "5"}]), "header", "customer_name"),
            (editor_post(self.school, [{}]), "lines", "Add at least one line."),
            (editor_post(self.school, [{"quantity": "1", "unit_price": "5"}]), "line", "description"),
            (editor_post(self.school, [{"product": self.soap, "quantity": "0"}]), "line", "quantity"),
            (editor_post(self.school, [{"product": self.soap, "quantity": "1.234"}]), "line", "quantity"),
            (editor_post(self.school, [{"product": self.soap, "quantity": "1", "line_discount": "81"}]), "line", "line_discount"),
            (editor_post(self.school, [{"product": self.soap, "quantity": "1"}], due_date="2026-10-01"), "header", "due_date"),
            (editor_post(self.school, [{"product": self.soap, "quantity": "1"}], invoice_discount="81"), "header_msg",
             "Invoice discount cannot be more than the subtotal."),
        ]
        for data, where, expected in cases:
            with self.subTest(expected=expected):
                r = self.client.post(url, data)
                self.assertEqual(r.status_code, 200)
                if where == "header":
                    self.assertIn(expected, r.context["header"].errors)
                elif where == "line":
                    self.assertIn(expected, r.context["lines"].forms[0].errors)
                elif where == "lines":
                    self.assertIn(expected, r.context["lines"].non_form_errors())
                else:
                    self.assertContains(r, expected)
        self.assertFalse(Invoice.objects.exists())

    def test_archived_product_cannot_be_added_but_stays_on_existing_draft(self):
        old = make_product("Old soap", package_size="")
        draft = services.create_draft(user=self.user, customer=self.school)
        services.add_line(draft, product=old, quantity="1")
        old.is_active = False
        old.save()
        r = self.client.post(reverse("invoices:create"), editor_post(self.school, [{"product": old, "quantity": "1"}]))
        self.assertIn("product", r.context["lines"].forms[0].errors)
        # On the existing draft it remains selectable, so the draft can still be saved.
        r = self.client.post(reverse("invoices:edit", args=[draft.pk]), editor_post(self.school, [{"product": old, "quantity": "2"}]))
        self.assertRedirects(r, reverse("invoices:detail", args=[draft.pk]))

    def test_edit_draft_replaces_lines_in_order(self):
        draft = services.create_draft(user=self.user, customer=self.school)
        services.add_line(draft, product=self.soap, quantity="1")
        r = self.client.get(reverse("invoices:edit", args=[draft.pk]))
        self.assertEqual(r.context["lines"].forms[0].initial["product"], self.soap.pk)
        self.client.post(reverse("invoices:edit", args=[draft.pk]), editor_post(self.school, [
            {"product": self.liquid, "quantity": "1"}, {"product": self.soap, "quantity": "3"}]))
        self.assertEqual([l.product for l in draft.lines.all()], [self.liquid, self.soap])
        draft.refresh_from_db()
        self.assertEqual(draft.total, D("890.00"))

    def test_save_and_keep_editing(self):
        r = self.client.post(reverse("invoices:create"),
                             {**editor_post(self.school, [{"product": self.soap, "quantity": "1"}]), "save_and_continue": "1"})
        self.assertRedirects(r, reverse("invoices:edit", args=[Invoice.objects.get().pk]))

    def test_issued_invoice_cannot_be_edited(self):
        inv = services.create_draft(user=self.user, customer=self.school)
        services.add_line(inv, product=self.soap, quantity="1")
        inv = services.issue_invoice(inv, self.user)
        # The draft editor sends issued invoices to Amend, which keeps the history.
        r = self.client.get(reverse("invoices:edit", args=[inv.pk]))
        self.assertRedirects(r, reverse("invoices:amend", args=[inv.pk]))
        r = self.client.post(reverse("invoices:edit", args=[inv.pk]), editor_post(self.school, [{"product": self.soap, "quantity": "50"}]))
        self.assertRedirects(r, reverse("invoices:amend", args=[inv.pk]), fetch_redirect_response=False)
        inv.refresh_from_db()
        self.assertEqual((inv.lines.get().quantity, inv.revision), (D("1.00"), 1))

    def test_drafts_follow_current_tax_settings(self):
        configure_business(tax_enabled=True, tax_rate=D("16"), tax_label="VAT")
        self.client.post(reverse("invoices:create"), editor_post(self.school, [{"product": self.liquid, "quantity": "1"}]))
        inv = Invoice.objects.get()
        self.assertEqual((inv.tax_rate, inv.tax_total, inv.total), (D("16.00"), D("104.00"), D("754.00")))


class LifecycleViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business()
        self.client.force_login(self.user)
        self.soap = make_product("Bar soap", "80.00", package_size="200 g", unit="bar")
        record_stock_in(self.soap, "20", self.user)
        self.school = make_customer("Test School")

    def draft(self, qty="5"):
        inv = services.create_draft(user=self.user, customer=self.school)
        services.add_line(inv, product=self.soap, quantity=qty)
        return inv

    def stock(self):
        self.soap.refresh_from_db()
        return self.soap.stock_qty

    def test_detail_warns_about_stock_before_issue(self):
        r = self.client.get(reverse("invoices:detail", args=[self.draft("25").pk]))
        self.assertContains(r, "Not enough stock to issue this invoice")

    def test_issue(self):
        inv = self.draft()
        self.assertEqual(self.client.get(reverse("invoices:issue", args=[inv.pk])).status_code, 405)
        r = self.client.post(reverse("invoices:issue", args=[inv.pk]), follow=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, "issued")
        self.assertContains(r, f"Invoice {inv.number} issued")
        self.assertEqual(self.stock(), D("15"))

    def test_issue_blocked_by_stock_shows_message(self):
        inv = self.draft("25")
        r = self.client.post(reverse("invoices:issue", args=[inv.pk]), follow=True)
        self.assertContains(r, "Not enough stock")
        inv.refresh_from_db()
        self.assertEqual(inv.status, "draft")
        self.assertEqual(self.stock(), D("20"))

    def test_issue_twice_is_harmless(self):
        inv = self.draft()
        self.client.post(reverse("invoices:issue", args=[inv.pk]))
        r = self.client.post(reverse("invoices:issue", args=[inv.pk]), follow=True)
        self.assertContains(r, "cannot be edited")
        self.assertEqual(self.stock(), D("15"))

    def test_cancel_requires_reason_then_returns_stock(self):
        inv = services.issue_invoice(self.draft(), self.user)
        url = reverse("invoices:cancel", args=[inv.pk])
        self.assertContains(self.client.get(url), "Cancel invoice only")
        r = self.client.post(url, {"reason": ""})
        self.assertIn("reason", r.context["form"].errors)
        r = self.client.post(url, {"reason": "Customer changed order"})
        self.assertRedirects(r, reverse("invoices:detail", args=[inv.pk]))
        inv.refresh_from_db()
        self.assertEqual((inv.status, inv.cancel_reason), ("cancelled", "Customer changed order"))
        self.assertEqual(self.stock(), D("20"))
        self.assertContains(self.client.get(reverse("invoices:detail", args=[inv.pk])), "Create replacement invoice")

    def test_cancel_and_replace_in_one_step(self):
        inv = services.issue_invoice(self.draft(), self.user)
        r = self.client.post(reverse("invoices:cancel", args=[inv.pk]), {"reason": "Wrong qty", "replace": "1"})
        replacement = Invoice.objects.get(replaces=inv)
        self.assertRedirects(r, reverse("invoices:edit", args=[replacement.pk]))
        self.assertEqual(replacement.lines.get().quantity, D("5"))
        # fix the quantity and issue the replacement: stock nets out correctly
        self.client.post(reverse("invoices:edit", args=[replacement.pk]), editor_post(self.school, [{"product": self.soap, "quantity": "3"}]))
        self.client.post(reverse("invoices:issue", args=[replacement.pk]))
        self.assertEqual(self.stock(), D("17"))
        self.assertEqual(find_stock_discrepancies(), [])
        r = self.client.get(reverse("invoices:detail", args=[inv.pk]))
        self.assertContains(r, f"Replaced by")

    def test_cancel_with_payments_explains_the_credit(self):
        inv = services.issue_invoice(self.draft(), self.user)
        pay = Payment.objects.create(payer_name="Test School", amount=D("100"), created_by=self.user,
                                     method=PaymentMethod.objects.get(name="Cash"))
        PaymentAllocation.objects.create(payment=pay, invoice=inv, amount=D("100"))
        self.assertContains(self.client.get(reverse("invoices:cancel", args=[inv.pk])), "KES 100.00")
        r = self.client.post(reverse("invoices:cancel", args=[inv.pk]), {"reason": "x"}, follow=True)
        self.assertContains(r, "now credit")
        inv.refresh_from_db()
        self.assertEqual(inv.status, "cancelled")

    def test_duplicate_uses_todays_prices(self):
        inv = services.issue_invoice(self.draft(), self.user)
        set_price(self.soap, "90.00")
        r = self.client.post(reverse("invoices:duplicate", args=[inv.pk]))
        copy = Invoice.objects.exclude(pk=inv.pk).get()
        self.assertRedirects(r, reverse("invoices:edit", args=[copy.pk]))
        self.assertEqual((copy.status, copy.customer, copy.replaces), ("draft", self.school, None))
        self.assertEqual(copy.lines.get().unit_price, D("90.00"))
        self.assertEqual(copy.total, D("450.00"))

    def test_delete_draft_only(self):
        draft = self.draft()
        self.client.post(reverse("invoices:delete", args=[draft.pk]))
        self.assertFalse(Invoice.objects.filter(pk=draft.pk).exists())
        issued = services.issue_invoice(self.draft(), self.user)
        self.client.post(reverse("invoices:delete", args=[issued.pk]))
        self.assertTrue(Invoice.objects.filter(pk=issued.pk).exists())


class ListTests(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business()
        self.client.force_login(self.user)
        p = make_product("Soap", "100.00", track_stock=False, package_size="")
        self.a, self.b = make_customer("Alpha School"), make_customer("Beta Hospital")
        cash = PaymentMethod.objects.get(name="Cash")

        def issued(customer, day, due=None, paid=None):
            inv = services.create_draft(user=self.user, customer=customer, issue_date=datetime.date(2026, 10, day), due_date=due)
            services.add_line(inv, product=p, quantity="10")  # 1,000
            inv = services.issue_invoice(inv, self.user)
            if paid:
                pay = Payment.objects.create(payer_name="x", amount=D(paid), method=cash, created_by=self.user)
                PaymentAllocation.objects.create(payment=pay, invoice=inv, amount=D(paid))
            return inv

        self.unpaid = issued(self.a, 1)
        self.partial = issued(self.a, 2, paid="400")
        self.paid = issued(self.b, 3, paid="1000")
        self.overdue = issued(self.b, 4, due=datetime.date(2026, 10, 5))
        self.cancelled = services.cancel_invoice(issued(self.b, 5), self.user, "test")
        self.draft = services.create_draft(user=self.user, customer=self.a)
        services.add_line(self.draft, product=p, quantity="1")

    def numbers(self, **params):
        r = self.client.get(reverse("invoices:list"), params)
        return {inv.pk for inv in r.context["page"].object_list}

    def test_state_filters(self):
        self.assertEqual(self.numbers(state="unpaid"), {self.unpaid.pk})
        self.assertEqual(self.numbers(state="partial"), {self.partial.pk})
        self.assertEqual(self.numbers(state="paid"), {self.paid.pk})
        self.assertEqual(self.numbers(state="overdue"), {self.overdue.pk})
        self.assertEqual(self.numbers(state="cancelled"), {self.cancelled.pk})
        self.assertEqual(self.numbers(state="draft"), {self.draft.pk})
        self.assertEqual(self.numbers(state="outstanding"), {self.unpaid.pk, self.partial.pk, self.overdue.pk})
        self.assertEqual(len(self.numbers()), 6)

    def test_search_customer_and_dates(self):
        self.assertEqual(self.numbers(q=self.paid.number), {self.paid.pk})
        self.assertEqual(self.numbers(q="beta"), {self.paid.pk, self.overdue.pk, self.cancelled.pk})
        self.assertEqual(self.numbers(customer=self.a.pk), {self.unpaid.pk, self.partial.pk, self.draft.pk})
        self.assertEqual(self.numbers(date_from="2026-10-02", date_to="2026-10-03"), {self.partial.pk, self.paid.pk})

    def test_totals_count_issued_only(self):
        totals = self.client.get(reverse("invoices:list")).context["totals"]
        self.assertEqual((totals["total"], totals["paid"], totals["balance"]), (D("4000"), D("1400"), D("2600")))

    def test_customer_page_links_invoices(self):
        r = self.client.get(reverse("customers:detail", args=[self.a.pk]))
        self.assertContains(r, reverse("invoices:detail", args=[self.unpaid.pk]))
        self.assertEqual(r.context["outstanding"], D("1600.00"))


class InvoicePermissionTests(TestCase):
    def test_view_only_staff_cannot_issue_or_cancel(self):
        owner = make_user()
        configure_business()
        p = make_product(track_stock=False)
        inv = services.create_draft(user=owner, customer_name="X")
        services.add_line(inv, product=p, quantity="1")
        self.client.force_login(make_staff(perms=("invoices.view_invoice",)))
        self.assertEqual(self.client.get(reverse("invoices:detail", args=[inv.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("invoices:image", args=[inv.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("invoices:issue", args=[inv.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("invoices:create")).status_code, 403)
        self.assertEqual(self.client.post(reverse("invoices:delete", args=[inv.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("invoices:detail", args=[inv.pk])), "Issue invoice</button>")

    def test_anonymous_cannot_download_image(self):
        r = self.client.get(reverse("invoices:image", args=[1]))
        self.assertEqual(r.status_code, 302)
