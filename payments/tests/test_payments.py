import datetime
import uuid
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, ImmutableRecordError
from core.testing import configure_business, make_customer, make_product, make_staff, make_user
from inventory.services import record_stock_in
from invoices import services as invoices
from invoices.models import Invoice
from payments import services
from payments.models import Payment, PaymentAllocation, PaymentMethod
from payments.services import Allocation


class PaymentTestBase(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business()
        self.school = make_customer("Test School", kind="organization")
        self.other = make_customer("Other Customer")
        self.soap = make_product("Bar soap", "100.00", package_size="", unit="bar")
        record_stock_in(self.soap, "1000", self.user)
        self.cash = PaymentMethod.objects.get(name="Cash")
        self.mpesa = PaymentMethod.objects.get(name="M-Pesa")
        self.inv1 = self.issue(self.school, 10, days_ago=20)   # 1,000
        self.inv2 = self.issue(self.school, 5, days_ago=5)     # 500

    def issue(self, customer, qty, days_ago=0, due_days=7):
        date = timezone.localdate() - datetime.timedelta(days=days_ago)
        inv = invoices.create_draft(user=self.user, customer=customer, issue_date=date,
                                    due_date=date + datetime.timedelta(days=due_days))
        invoices.add_line(inv, product=self.soap, quantity=str(qty))
        return invoices.issue_invoice(inv, self.user)

    def pay(self, amount, allocations=(), method=None, reference="", customer=None, **kw):
        return services.record_payment(user=self.user, amount=D(amount), method=method or self.cash,
                                       customer=customer if customer is not None else self.school,
                                       reference=reference, allocations=[Allocation(i, D(a)) for i, a in allocations], **kw)

    def paid(self, inv):
        return Invoice.objects.with_payment_totals().get(pk=inv.pk).amount_paid


class RecordPaymentTests(PaymentTestBase):
    def test_full_and_partial_payments_update_balances(self):
        self.pay("200", [(self.inv2, "200")])
        self.assertEqual(self.paid(self.inv2), D("200.00"))
        self.assertEqual(Invoice.objects.get(pk=self.inv2.pk).payment_state(), "partial")
        self.assertEqual(Invoice.objects.get(pk=self.inv1.pk).payment_state(), "overdue")
        self.pay("300", [(self.inv2, "300")])
        self.assertEqual(Invoice.objects.get(pk=self.inv2.pk).payment_state(), "paid")

    def test_one_payment_split_across_invoices_with_credit_left(self):
        p = self.pay("1700", [(self.inv1, "1000"), (self.inv2, "500")])
        self.assertEqual((p.get_allocated(), p.get_unallocated()), (D("1500.00"), D("200.00")))
        self.assertEqual(services.customer_credit(self.school), D("200.00"))
        self.assertTrue(AuditLog.objects.filter(action="payment.recorded", object_id=str(p.pk)).exists())

    def test_rules(self):
        cases = [
            ("0", [], {}, "more than zero"),
            ("100", [(self.inv1, "60"), (self.inv2, "60")], {}, "only"),          # more than the payment
            ("2000", [(self.inv1, "1200")], {}, "more than the"),                 # more than the balance
            ("100", [], {"method": self.mpesa}, "reference"),                    # M-Pesa needs a code
            ("100", [], {"payment_date": timezone.localdate() + datetime.timedelta(days=1)}, "future"),
        ]
        for amount, allocs, kw, message in cases:
            with self.subTest(message=message), self.assertRaisesMessage(ValidationError, message):
                self.pay(amount, allocs, **kw)
        draft = invoices.create_draft(user=self.user, customer=self.school)
        with self.assertRaisesMessage(ValidationError, "issued invoices"):
            self.pay("10", [(draft, "10")])
        other_inv = self.issue(self.other, 1)
        with self.assertRaisesMessage(ValidationError, "different customer"):
            self.pay("10", [(other_inv, "10")])
        self.assertFalse(Payment.objects.exists())  # nothing half-saved

    def test_same_reference_and_same_form_cannot_be_recorded_twice(self):
        self.pay("100", method=self.mpesa, reference="sgh12abc")
        self.assertEqual(Payment.objects.get().reference, "SGH12ABC")
        with self.assertRaisesMessage(ValidationError, "already recorded"):
            self.pay("100", method=self.mpesa, reference="SGH12ABC ")
        token = uuid.uuid4()
        self.pay("50", submission_token=token)
        with self.assertRaises(services.DuplicateSubmission):
            self.pay("50", submission_token=token)
        self.assertEqual(Payment.objects.count(), 2)

    def test_payments_and_allocations_cannot_be_edited_or_deleted(self):
        p = self.pay("100", [(self.inv1, "100")])
        p.amount = D("1")
        with self.assertRaises(ImmutableRecordError):
            p.save()
        with self.assertRaises(ImmutableRecordError):
            Payment.objects.get(pk=p.pk).delete()
        a = p.allocations.get()
        a.amount = D("5")
        with self.assertRaises(ImmutableRecordError):
            a.save()
        with self.assertRaises(ImmutableRecordError):
            a.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentAllocation.objects.create(payment=p, invoice=self.inv1, amount=D("1"))  # one live link


class CreditReversalAndCancellationTests(PaymentTestBase):
    def test_apply_credit_later(self):
        p = self.pay("700")                                          # all credit
        services.apply_credit(p, [Allocation(self.inv2, D("500"))], self.user)
        self.assertEqual((self.paid(self.inv2), p.get_unallocated()), (D("500.00"), D("200.00")))
        with self.assertRaisesMessage(ValidationError, "free to apply"):
            services.apply_credit(p, [Allocation(self.inv1, D("300"))], self.user)

    def test_reversal_reopens_invoices_and_frees_the_reference(self):
        p = self.pay("1000", [(self.inv1, "1000")], method=self.mpesa, reference="ABC123")
        with self.assertRaises(ValidationError):
            services.reverse_payment(p, self.user, "  ")
        services.reverse_payment(p, self.user, "Entered twice")
        p.refresh_from_db()
        self.assertTrue(p.is_reversed)
        self.assertEqual((self.paid(self.inv1), p.get_unallocated()), (D("0.00"), D("0.00")))
        with self.assertRaises(ValidationError):
            services.reverse_payment(p, self.user, "again")
        p.is_reversed = False
        with self.assertRaises(ImmutableRecordError):
            p.save()
        self.pay("1000", [(self.inv1, "1000")], method=self.mpesa, reference="ABC123")  # re-entered correctly

    def test_cancel_releases_payments_and_the_replacement_receives_them(self):
        p = self.pay("1200", [(self.inv1, "1000"), (self.inv2, "200")])
        cancelled = invoices.cancel_invoice(self.inv1, self.user, "Wrong quantity")
        self.assertEqual(cancelled.released_payments, D("1000.00"))
        self.assertEqual(p.get_unallocated(), D("1000.00"))
        draft = invoices.create_replacement_draft(cancelled, self.user)
        draft.lines.get().delete()
        invoices.add_line(draft, product=self.soap, quantity="8")      # 800 now
        new = invoices.issue_invoice(draft, self.user)
        self.assertEqual((new.moved_payments, self.paid(new)), (D("800.00"), D("800.00")))
        self.assertEqual(p.get_unallocated(), D("200.00"))                # the rest stays as credit
        released = PaymentAllocation.objects.get(invoice=cancelled)
        self.assertIsNotNone(released.released_at)
        self.assertIn("Wrong quantity", released.release_reason)

    def test_amending_below_paid_is_blocked(self):
        self.pay("1000", [(self.inv1, "1000")])
        with self.assertRaises(invoices.InvoiceStateError):
            invoices.amend_invoice(self.inv1, user=self.user, reason="less", lines=[{"product": self.soap, "quantity": D("5")}],
                                   header={"customer": self.school, "issue_date": self.inv1.issue_date})


class PaymentViewTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def post(self, data, query=""):
        base = {"customer": self.school.pk, "payer_name": "", "amount": "", "payment_date": str(timezone.localdate()),
                "method": self.cash.pk, "reference": "", "notes": "", "token": str(uuid.uuid4())}
        base.update(data)
        return self.client.post(reverse("payments:create") + query, base)

    def test_form_from_an_invoice_is_prefilled(self):
        r = self.client.get(reverse("payments:create"), {"invoice": self.inv2.pk})
        form = r.context["form"]
        self.assertEqual(form.initial["customer"], self.school)
        self.assertEqual((form.initial["amount"], form.initial[f"alloc_{self.inv2.pk}"]), (D("500.00"), D("500.00")))
        self.assertEqual([i.number for i in form.invoices], [self.inv1.number, self.inv2.number])  # oldest first

    def test_record_split_payment(self):
        r = self.post({"amount": "1200", f"alloc_{self.inv1.pk}": "1000", f"alloc_{self.inv2.pk}": "200",
                       "next": reverse("customers:detail", args=[self.school.pk])})
        self.assertRedirects(r, reverse("customers:detail", args=[self.school.pk]))
        p = Payment.objects.get()
        self.assertEqual((p.amount, p.payer_name, p.get_unallocated()), (D("1200.00"), "Test School", D("0.00")))
        page = self.client.get(reverse("customers:detail", args=[self.school.pk]))
        self.assertContains(page, "KES 300.00")   # still owed

    def test_errors_keep_the_form(self):
        r = self.post({"amount": "100", f"alloc_{self.inv1.pk}": "100", f"alloc_{self.inv2.pk}": "50"})
        self.assertContains(r, "more to invoices than the amount received")
        r = self.post({"amount": "5000", f"alloc_{self.inv2.pk}": "600"})
        self.assertContains(r, "still due on this invoice")
        r = self.post({"amount": "100", "method": self.mpesa.pk})
        self.assertContains(r, "Enter the M-Pesa reference")
        r = self.post({"customer": "", "amount": "100"})
        self.assertContains(r, "Say who paid")
        self.assertFalse(Payment.objects.exists())

    def test_one_time_customer_invoice(self):
        inv = invoices.create_draft(user=self.user, customer_name="Walk-in buyer")
        invoices.add_line(inv, product=self.soap, quantity="2")
        inv = invoices.issue_invoice(inv, self.user)
        q = f"?invoice={inv.pk}"
        r = self.client.get(reverse("payments:create") + q)
        self.assertEqual(r.context["form"].initial["payer_name"], "Walk-in buyer")
        self.post({"customer": "", "payer_name": "Walk-in buyer", "amount": "200", f"alloc_{inv.pk}": "200"}, q)
        self.assertEqual(self.paid(inv), D("200.00"))

    def test_detail_apply_credit_reverse_and_receipt(self):
        p = self.pay("700")
        r = self.client.get(reverse("payments:detail", args=[p.pk]))
        self.assertContains(r, "Apply the credit")
        self.client.post(reverse("payments:apply", args=[p.pk]), {f"alloc_{self.inv2.pk}": "500"})
        self.assertEqual(self.paid(self.inv2), D("500.00"))
        # One-click credit from the invoice page.
        inv_page = self.client.get(reverse("invoices:detail", args=[self.inv1.pk]))
        self.assertContains(inv_page, "Use KES 200.00 credit")
        self.client.post(reverse("payments:apply_to_invoice", args=[p.pk, self.inv1.pk]))
        self.assertEqual(self.paid(self.inv1), D("200.00"))
        receipt = self.client.get(reverse("payments:receipt", args=[p.pk]))
        self.assertEqual(receipt["Content-Type"], "application/pdf")
        self.assertIn(b"%PDF", receipt.getvalue()[:8])
        r = self.client.post(reverse("payments:reverse", args=[p.pk]), {"reason": "Cheque bounced"})
        self.assertRedirects(r, reverse("payments:detail", args=[p.pk]))
        self.assertEqual((self.paid(self.inv1), self.paid(self.inv2)), (D("0.00"), D("0.00")))

    def test_list_filters_and_totals(self):
        self.pay("100", method=self.mpesa, reference="QWE1")
        p2 = self.pay("50")
        services.reverse_payment(p2, self.user, "Mistake")
        url = reverse("payments:list")
        r = self.client.get(url)
        self.assertEqual(r.context["received"], D("100.00"))
        self.assertEqual(len(self.client.get(url, {"state": "reversed"}).context["page"].object_list), 1)
        self.assertEqual(len(self.client.get(url, {"q": "qwe1"}).context["page"].object_list), 1)
        self.assertEqual(len(self.client.get(url, {"state": "credit"}).context["page"].object_list), 1)

    def test_permissions(self):
        p = self.pay("100")
        viewer = make_staff(perms=("payments.view_payment", "invoices.view_invoice"))
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("payments:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("payments:create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("payments:reverse", args=[p.pk])).status_code, 403)
        self.client.force_login(make_staff(username="nobody"))
        self.assertEqual(self.client.get(reverse("payments:list")).status_code, 403)


class DashboardTests(PaymentTestBase):
    def test_figures(self):
        self.client.force_login(self.user)
        self.pay("300", [(self.inv2, "300")])
        r = self.client.get(reverse("core:dashboard"))
        month = next(p for p in r.context["periods"] if p["label"] == "This month")
        today = r.context["periods"][0]
        self.assertEqual(today["received"], D("300.00"))
        self.assertEqual(r.context["outstanding"]["balance"], D("1200.00"))   # 1,000 + 200
        self.assertEqual(r.context["overdue"]["balance"], D("1000.00"))       # inv1 was due 13 days ago
        self.assertEqual(r.context["best_sellers"][0]["product"], self.soap)
        if self.inv1.issue_date.month == timezone.localdate().month:
            self.assertEqual(month["invoiced"], D("1500.00"))
        self.assertContains(r, "Best sellers this month")

    def test_staff_without_money_permissions_see_only_stock(self):
        self.client.force_login(make_staff())
        r = self.client.get(reverse("core:dashboard"))
        self.assertFalse(r.context["show_money"])
        self.assertNotContains(r, "Invoiced and received")
