"""Customer types (individual / institution): prices, payment terms, the
invoice editor's auto-fill, and the WhatsApp / email share buttons."""

import datetime
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from core.forms import BusinessSettingsForm
from core.models import BusinessSettings
from core.testing import configure_business, make_customer, make_option, make_product, make_user, unit
from invoices import services
from invoices.models import Invoice
from invoices.sharing import share_message, whatsapp_number
from invoices.tests.test_views import editor_post
from products.models import SellingOption
from products.pricing import price_for

INDIVIDUAL, INSTITUTION = "individual", "organization"


class PriceForTests(TestCase):
    def test_institutions_pay_the_institution_price_when_set(self):
        p = make_product("Bar soap", "80.00", unit="bar")
        option = p.default_option
        self.assertEqual(price_for(option, INSTITUTION), D("80.00"))  # not set: same price
        option.institution_price = D("70.00")
        self.assertEqual(price_for(option, INSTITUTION), D("70.00"))
        self.assertEqual(price_for(option, INDIVIDUAL), D("80.00"))
        self.assertEqual(price_for(option, ""), D("80.00"))  # invoices from before customer types


class InvoicePricingTests(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business(default_payment_terms_days=0, default_payment_terms="Pay on delivery",
                           institution_payment_terms_days=30, institution_payment_terms="Pay within 30 days")
        self.soap = make_product("Bar soap", "80.00", unit="bar", track_stock=False)
        option = self.soap.default_option
        option.institution_price = D("70.00")
        option.save()
        self.liquid = make_product("Liquid soap", stock_unit="ml", track_stock=False)
        make_option(self.liquid, per="l", price="250", default=True)  # no institution price
        self.school = make_customer("Test School", kind=INSTITUTION)
        self.person = make_customer("Test Person", kind=INDIVIDUAL)

    def test_draft_for_an_institution_uses_institution_prices_and_terms(self):
        inv = services.create_draft(user=self.user, customer=self.school, issue_date=datetime.date(2026, 11, 2))
        self.assertEqual(inv.customer_kind, INSTITUTION)
        self.assertEqual((inv.payment_terms, inv.due_date), ("Pay within 30 days", datetime.date(2026, 12, 2)))
        soap = services.add_line(inv, product=self.soap, quantity="3")
        liquid = services.add_line(inv, product=self.liquid, quantity="2", unit="l")
        self.assertEqual((soap.unit_price, soap.catalogue_price, soap.line_total), (D("70.00"), D("70.00"), D("210.00")))
        self.assertFalse(soap.is_price_overridden)
        self.assertEqual(liquid.unit_price, D("250.00"))  # no institution price set: the normal price

    def test_draft_for_an_individual_uses_normal_prices_and_terms(self):
        inv = services.create_draft(user=self.user, customer=self.person, issue_date=datetime.date(2026, 11, 2))
        self.assertEqual((inv.customer_kind, inv.payment_terms, inv.due_date),
                         (INDIVIDUAL, "Pay on delivery", datetime.date(2026, 11, 2)))
        self.assertEqual(services.add_line(inv, product=self.soap, quantity="3").unit_price, D("80.00"))

    def test_one_time_customer_defaults_to_individual_unless_told(self):
        inv = services.create_draft(user=self.user, customer_name="Walk-in")
        self.assertEqual(inv.customer_kind, INDIVIDUAL)
        inv = services.create_draft(user=self.user, customer_name="Visiting clinic", customer_kind=INSTITUTION)
        self.assertEqual(services.add_line(inv, product=self.soap, quantity="1").unit_price, D("70.00"))

    def test_typed_price_wins_over_the_institution_price(self):
        inv = services.create_draft(user=self.user, customer=self.school)
        line = services.add_line(inv, product=self.soap, quantity="1", unit_price="65")
        self.assertEqual((line.unit_price, line.catalogue_price), (D("65.00"), D("70.00")))
        self.assertTrue(line.is_price_overridden)

    def test_issued_invoice_keeps_its_price_when_the_catalogue_changes(self):
        inv = services.create_draft(user=self.user, customer=self.school)
        services.add_line(inv, product=self.soap, quantity="2")
        inv = services.issue_invoice(inv, self.user)
        option = self.soap.default_option
        option.institution_price = D("60.00")
        option.save()
        inv.refresh_from_db()
        self.assertEqual(inv.total, D("140.00"))

    def test_duplicate_uses_the_customers_current_type(self):
        inv = services.create_draft(user=self.user, customer=self.person)
        services.add_line(inv, product=self.soap, quantity="2")
        self.person.kind = INSTITUTION
        self.person.save()
        copy = services.duplicate_invoice(inv, self.user)
        self.assertEqual(copy.customer_kind, INSTITUTION)
        self.assertEqual(copy.payment_terms, inv.payment_terms)  # terms text is copied from the source
        self.assertEqual(copy.lines.get().unit_price, D("70.00"))

    def test_revision_snapshot_keeps_the_type(self):
        inv = services.create_draft(user=self.user, customer=self.school)
        services.add_line(inv, product=self.soap, quantity="1")
        inv = services.issue_invoice(inv, self.user)
        self.assertEqual(services.invoice_snapshot(inv)["customer_kind"], INSTITUTION)


class EditorCustomerTypeTests(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business(default_payment_terms_days=7, default_payment_terms="Pay within 7 days",
                           institution_payment_terms_days=30, institution_payment_terms="Pay within 30 days")
        self.client.force_login(self.user)
        self.soap = make_product("Bar soap", "80.00", unit="bar", track_stock=False)
        option = self.soap.default_option
        option.institution_price = D("70.00")
        option.save()
        self.school = make_customer("Test School", kind=INSTITUTION)
        self.person = make_customer("Test Person", kind=INDIVIDUAL)

    def test_new_invoice_for_a_saved_institution_starts_with_its_type_and_terms(self):
        r = self.client.get(reverse("invoices:create"), {"customer": self.school.pk})
        initial = r.context["header"].initial
        self.assertEqual((initial["customer_kind"], initial["payment_terms"]), (INSTITUTION, "Pay within 30 days"))
        self.assertEqual(initial["due_date"] - initial["issue_date"], datetime.timedelta(days=30))
        data = r.context["editor_data"]
        self.assertEqual(data["customers"][str(self.school.pk)]["kind"], INSTITUTION)
        self.assertEqual(data["paymentDefaults"][INSTITUTION], {"days": 30, "terms": "Pay within 30 days"})
        self.assertEqual(data["paymentDefaults"][INDIVIDUAL], {"days": 7, "terms": "Pay within 7 days"})
        soap = next(p for p in data["products"] if p["id"] == self.soap.pk)
        self.assertEqual((soap["options"][0]["price"], soap["options"][0]["institutionPrice"]), ("80.00", "70.00"))
        self.assertContains(r, 'name="customer_kind"')

    def test_blank_price_follows_the_chosen_type(self):
        lines = [{"product": self.soap, "quantity": "2"}]
        self.client.post(reverse("invoices:create"), editor_post(self.school, lines, customer_kind=INSTITUTION))
        self.client.post(reverse("invoices:create"), editor_post(self.school, lines, customer_kind=INDIVIDUAL))
        school_inv, as_individual = Invoice.objects.order_by("pk")
        self.assertEqual((school_inv.customer_kind, school_inv.total), (INSTITUTION, D("140.00")))
        # Changed by hand on the invoice: the customer record still says institution.
        self.assertEqual((as_individual.customer_kind, as_individual.total), (INDIVIDUAL, D("160.00")))
        self.school.refresh_from_db()
        self.assertEqual(self.school.kind, INSTITUTION)

    def test_type_not_sent_falls_back_to_the_saved_customer(self):
        self.client.post(reverse("invoices:create"), editor_post(self.school, [{"product": self.soap, "quantity": "1"}]))
        self.assertEqual(Invoice.objects.get().customer_kind, INSTITUTION)

    def test_discount_is_checked_against_the_institution_price(self):
        data = editor_post(self.school, [{"product": self.soap, "quantity": "1", "line_discount": "75"}],
                           customer_kind=INSTITUTION)
        r = self.client.post(reverse("invoices:create"), data)
        self.assertIn("line_discount", r.context["lines"].forms[0].errors)  # 75 > 70

    def test_editing_an_old_draft_without_a_type_uses_the_customers(self):
        inv = services.create_draft(user=self.user, customer=self.school)
        Invoice.objects.filter(pk=inv.pk).update(customer_kind="")  # as if made before customer types
        r = self.client.get(reverse("invoices:edit", args=[inv.pk]))
        self.assertEqual(r.context["header"].initial["customer_kind"], INSTITUTION)


class SettingsAndProductFormTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_settings_have_payment_terms_for_each_type(self):
        s = configure_business(default_payment_terms_days=None, institution_payment_terms_days=None)
        self.assertEqual(s.payment_defaults(INSTITUTION), (None, ""))
        form = BusinessSettingsForm(instance=s)
        titles = [title for title, _help, _fields in form.SECTIONS]
        self.assertIn("Payment terms", titles)
        self.assertEqual(form.fields["institution_payment_terms_days"].label, "Institutions: payment due after (days)")
        s.institution_payment_terms_days, s.institution_payment_terms = 30, "30 days"
        s.save()
        self.assertEqual(BusinessSettings.load().payment_defaults(INSTITUTION), (30, "30 days"))
        self.assertEqual(BusinessSettings.load().payment_defaults(INDIVIDUAL), (None, ""))

    def test_selling_option_institution_price_is_optional(self):
        p = make_product("Liquid soap", stock_unit="ml")
        url = reverse("products:option_create", args=[p.pk])
        base = {"method": "measure", "price_unit": unit("l").pk, "price": "250", "label": "", "is_active": "on"}
        self.client.post(url, {**base, "institution_price": "220"})
        self.client.post(url, {**base, "label": "loose", "institution_price": ""})
        with_price, without = SellingOption.objects.filter(product=p).order_by("pk")
        self.assertEqual((with_price.price, with_price.institution_price), (D("250.00"), D("220.00")))
        self.assertIsNone(without.institution_price)
        r = self.client.post(url, {**base, "institution_price": "-1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(p.options.count(), 2)
        self.assertContains(self.client.get(reverse("products:detail", args=[p.pk])), "Institutions KES 220.00")

    def test_customer_type_is_called_institution(self):
        c = make_customer("Test Hospital", kind=INSTITUTION)
        self.assertEqual(c.get_kind_display(), "Institution")
        self.assertContains(self.client.get(reverse("customers:detail", args=[c.pk])), "Institution")


class ShareTests(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business(name="Test Business")
        self.client.force_login(self.user)
        self.soap = make_product("Bar soap", "80.00", unit="bar", track_stock=False)
        self.school = make_customer("Test School", kind=INSTITUTION, phone="0711 000000", email="office@example.com")

    def issued(self, **fields):
        inv = services.create_draft(user=self.user, customer=self.school, issue_date=datetime.date(2026, 11, 2),
                                    due_date=datetime.date(2026, 12, 2), **fields)
        services.add_line(inv, product=self.soap, quantity="3")
        return services.issue_invoice(inv, self.user)

    def test_whatsapp_numbers(self):
        cases = {"0711 000000": "254711000000", "+254 711 000 000": "254711000000", "254711000000": "254711000000",
                 "711000000": "254711000000", "0100 000000": "254100000000", "+44 20 7946 0000": "442079460000",
                 "": "", "12345": "", "not a phone": ""}
        for phone, expected in cases.items():
            with self.subTest(phone=phone):
                self.assertEqual(whatsapp_number(phone), expected)

    def test_issued_invoice_page_has_share_buttons_and_message(self):
        inv = self.issued()
        r = self.client.get(reverse("invoices:detail", args=[inv.pk]))
        share = r.context["share"]
        self.assertEqual(share["whatsapp"], "254711000000")
        self.assertEqual(share["email"], "office@example.com")
        self.assertEqual(share["imageUrl"], reverse("invoices:image", args=[inv.pk]))
        self.assertEqual(share["filename"], f"{inv.number}_test-school.png")
        self.assertEqual(share["subject"], f"Invoice {inv.number} from Test Business")
        for text in [f"invoice {inv.number} dated 2 Nov 2026 for KES 240.00", "Kindly pay by 2 Dec 2026", "Test Business"]:
            with self.subTest(text=text):
                self.assertIn(text, share["text"])
        self.assertContains(r, 'data-share="whatsapp"')
        self.assertContains(r, 'data-share="email"')
        self.assertContains(r, 'id="share-data"')
        self.assertContains(r, "dist/share.js")

    def test_message_mentions_the_balance_or_that_it_is_paid(self):
        inv = self.issued()
        self.assertIn("Balance due: KES 140.00 by 2 Dec 2026.", share_message(inv, {"name": "X"}, D("140.00")))
        self.assertIn("fully paid", share_message(inv, {"name": "X"}, D("0")))

    def test_drafts_and_cancelled_invoices_have_no_share_buttons(self):
        draft = services.create_draft(user=self.user, customer=self.school)
        services.add_line(draft, product=self.soap, quantity="1")
        r = self.client.get(reverse("invoices:detail", args=[draft.pk]))
        self.assertIsNone(r.context["share"])
        self.assertNotContains(r, "data-share=")
        cancelled = services.cancel_invoice(self.issued(), self.user, "Wrong customer")
        self.assertNotContains(self.client.get(reverse("invoices:detail", args=[cancelled.pk])), "data-share=")
