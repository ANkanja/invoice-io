import datetime
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from core.models import AuditLog
from core.testing import configure_business, make_customer, make_product, make_staff, make_user
from customers.models import Customer
from invoices import services as invoice_services
from payments.models import Payment, PaymentAllocation, PaymentMethod

VALID = {"kind": "organization", "name": "Test Primary School", "contact_person": "The Bursar",
         "phone": "0712  345 678", "email": "", "billing_address": "", "tax_pin": "", "notes": ""}


class CustomerViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business()
        self.client.force_login(self.user)

    def test_empty_state(self):
        self.assertContains(self.client.get(reverse("customers:list")), "No customers yet")

    def test_create_normalises_and_audits(self):
        r = self.client.post(reverse("customers:create"), VALID)
        c = Customer.objects.get()
        self.assertRedirects(r, reverse("customers:detail", args=[c.pk]))
        self.assertEqual(c.phone, "0712 345 678")
        self.assertTrue(AuditLog.objects.filter(action="customer.created").exists())

    def test_required_and_invalid_fields(self):
        r = self.client.post(reverse("customers:create"), {**VALID, "name": "", "phone": "call me", "email": "nope"})
        self.assertEqual(r.status_code, 200)
        form = r.context["form"]
        self.assertEqual(set(form.errors), {"name", "phone", "email"})
        self.assertFalse(Customer.objects.exists())

    def test_duplicate_warning_does_not_block(self):
        make_customer("Test Primary School", phone="0700 111222")
        r = self.client.post(reverse("customers:create"), VALID, follow=True)
        self.assertContains(r, "Check for duplicates")
        self.assertEqual(Customer.objects.count(), 2)

    def test_search_and_filters(self):
        make_customer("Alpha Hospital", kind="organization", phone="0712 345 678")
        make_customer("Jane Buyer", kind="individual", phone="0799 000111", email="jane@example.com")
        make_customer("Gone Ltd", kind="organization", is_active=False)
        url = reverse("customers:list")

        def names(**params):
            return [c.name for c in self.client.get(url, params).context["page"].object_list]

        self.assertEqual(names(q="alpha"), ["Alpha Hospital"])
        self.assertEqual(names(q="0712345678"), ["Alpha Hospital"])  # spaces in stored number don't matter
        self.assertEqual(names(q="+254 712-345678"), ["Alpha Hospital"])  # international format too
        self.assertEqual(names(q="jane@"), ["Jane Buyer"])
        self.assertEqual(names(kind="individual"), ["Jane Buyer"])
        self.assertEqual(names(), ["Alpha Hospital", "Jane Buyer"])
        self.assertEqual(names(status="archived"), ["Gone Ltd"])

    def test_detail_shows_invoices_and_balances(self):
        c = make_customer("Test School")
        p = make_product(track_stock=False)
        cash = PaymentMethod.objects.get(name="Cash")
        inv = invoice_services.create_draft(user=self.user, customer=c, issue_date=datetime.date(2026, 1, 1),
                                            due_date=datetime.date(2026, 1, 15))
        invoice_services.add_line(inv, product=p, quantity="10")      # 1,000.00
        inv = invoice_services.issue_invoice(inv, self.user)
        pay = Payment.objects.create(payer_name="Test School", amount=D("400"), method=cash, created_by=self.user)
        PaymentAllocation.objects.create(payment=pay, invoice=inv, amount=D("400"))
        draft = invoice_services.create_draft(user=self.user, customer=c)
        invoice_services.add_line(draft, product=p, quantity="1")     # drafts are not listed or counted

        r = self.client.get(reverse("customers:detail", args=[c.pk]))
        self.assertEqual(r.context["outstanding"], D("600.00"))
        self.assertEqual(r.context["overdue"], D("600.00"))
        self.assertEqual(r.context["invoiced_total"], D("1000.00"))
        self.assertEqual(len(r.context["page"].object_list), 1)
        self.assertContains(r, inv.number)
        self.assertContains(r, "Overdue")

    def test_edit_and_archive(self):
        c = make_customer("Old Name")
        self.client.post(reverse("customers:edit", args=[c.pk]), {**VALID, "name": "New Name"})
        c.refresh_from_db()
        self.assertEqual(c.name, "New Name")
        self.assertEqual(AuditLog.objects.get(action="customer.updated").changes["name"], ["Old Name", "New Name"])
        self.client.post(reverse("customers:archive", args=[c.pk]))
        c.refresh_from_db()
        self.assertFalse(c.is_active)
        self.client.post(reverse("customers:restore", args=[c.pk]))
        c.refresh_from_db()
        self.assertTrue(c.is_active)

    def test_permissions(self):
        c = make_customer()
        self.client.force_login(make_staff(perms=("customers.view_customer",)))
        self.assertEqual(self.client.get(reverse("customers:detail", args=[c.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("customers:edit", args=[c.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("customers:create"), VALID).status_code, 403)
