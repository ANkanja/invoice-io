"""Restock plans: suggestions, plans, purchases into stock, reversals, figures, pages."""

import datetime
import uuid
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import ImmutableRecordError
from core.testing import configure_business, make_customer, make_option, make_product, make_staff, make_user
from inventory.models import StockMovement
from inventory.services import record_stock_in
from invoices import services as invoices
from restock import services
from restock.models import RestockLine, RestockPlan, RestockPurchase

TODAY = datetime.date(2026, 11, 14)


class RestockTestCase(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business()
        # Liquid soap kept in ml, sold per litre; alert at 5 L.
        self.soap = make_product("Liquid soap", stock_unit="ml", low_stock_threshold=D("5000"))
        make_option(self.soap, per="l", price="250", default=True)
        record_stock_in(self.soap, "40000", self.user, unit_cost="120", entered=(D("40"), "L"))
        self.customer = make_customer("Test Hospital", kind="organization")

    def sell(self, litres):
        inv = invoices.create_draft(user=self.user, customer=self.customer)
        invoices.add_line(inv, product=self.soap, quantity=str(litres), unit="l")
        return invoices.issue_invoice(inv, self.user)

    def plan(self, **kw):
        return services.create_plan(month=datetime.date(2026, 12, 1), user=self.user, today=timezone.localdate(), **kw)


class SuggestionTests(RestockTestCase):
    def test_suggestion_is_sales_plus_alert_minus_stock(self):
        self.sell(30)  # 30 L over the last 3 months → 10 L a month; 10 L left
        self.soap.refresh_from_db()
        s = services.suggest(self.soap, timezone.localdate())
        self.assertEqual((s.monthly_sales, s.stock, s.quantity), (D("10.000"), D("10.000"), D("5")))  # 10 + 5 − 10
        self.assertEqual(services.unit_label(self.soap), "L")
        self.assertEqual(services.last_unit_cost(self.soap), D("120.00"))  # per litre, as typed on Stock in

    def test_rounds_up_to_whole_units_and_never_below_zero(self):
        self.sell(31)  # 10.333 L a month, 9 L left → 6.333 → 7 L
        self.soap.refresh_from_db()
        self.assertEqual(services.suggest(self.soap, timezone.localdate()).quantity, D("7"))
        plenty = make_product("Bar soap", "80.00", unit="bar")
        record_stock_in(plenty, "100", self.user)
        self.assertEqual(services.suggest(plenty, timezone.localdate()).quantity, D("0"))

    def test_cancelled_sales_and_old_sales_dont_count(self):
        inv = self.sell(30)
        invoices.cancel_invoice(inv, self.user, "Ordered twice")
        self.soap.refresh_from_db()
        self.assertEqual(services.monthly_sales(self.soap, timezone.localdate()), D("0"))
        self.sell(30)
        StockMovement.objects.filter(kind=StockMovement.Kind.SALE).update(
            created_at=timezone.now() - datetime.timedelta(days=120))
        self.assertEqual(services.monthly_sales(self.soap, timezone.localdate()), D("0"))

    def test_last_cost_per_litre_when_bought_in_containers(self):
        record_stock_in(self.soap, "20000", self.user, unit_cost="2500", entered=(D("4"), "5 L container"))
        self.assertEqual(services.last_unit_cost(self.soap), D("500.00"))  # 4 × 2,500 for 20 L


class PlanTests(RestockTestCase):
    def test_new_plan_lists_products_needing_stock_and_carries_other_items(self):
        self.sell(30)
        make_product("Brush", "50.00", unit="piece")  # tracked, no sales, 0 stock, no alert → not needed
        earlier = services.create_plan(month=datetime.date(2026, 11, 1), user=self.user)
        services.add_line(earlier, user=self.user, description="Caustic soda", unit="kg", quantity="25", unit_cost="180")
        plan = self.plan(budget="20000")
        lines = list(plan.lines.all())
        self.assertEqual([l.description for l in lines], ["Liquid soap", "Caustic soda"])
        soap, soda = lines
        self.assertEqual((soap.unit_label, soap.suggested_qty, soap.planned_qty, soap.planned_unit_cost),
                         ("L", D("5.000"), D("5.000"), D("120.00")))
        self.assertEqual((soda.product, soda.planned_qty, soda.planned_unit_cost), (None, D("25.000"), D("180.00")))
        self.assertEqual(plan.budget, D("20000.00"))
        with self.assertRaises(ValidationError):
            self.plan()  # one plan per month

    def test_add_and_save_lines(self):
        plan = self.plan()
        with self.assertRaises(ValidationError):
            services.add_line(plan, user=self.user, description="  ")
        line = services.add_line(plan, user=self.user, description="Transport", unit="trips", quantity="2", unit_cost="1500")
        brush = make_product("Brush", "50.00", unit="piece")
        services.add_line(plan, user=self.user, product=brush)
        with self.assertRaises(ValidationError):
            services.add_line(plan, user=self.user, product=brush)
        services.save_plan(plan, user=self.user, budget="5000", notes="Before school opens",
                           lines=[{"line": line, "quantity": D("3"), "unit_cost": D("1400")},
                                  {"line": plan.lines.get(product=brush), "remove": True}])
        line.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual((line.planned_qty, line.planned_unit_cost, line.planned_total), (D("3"), D("1400"), D("4200.00")))
        self.assertEqual((plan.budget, plan.notes), (D("5000.00"), "Before school opens"))
        self.assertFalse(plan.lines.filter(product=brush).exists())

    def test_figures_and_budget(self):
        plan = self.plan(budget="1000")
        services.add_line(plan, user=self.user, description="Labels", unit="rolls", quantity="3", unit_cost="333.335")
        figures = services.plan_figures(plan)
        self.assertEqual(figures.planned_total, D("1000.02"))  # 3 × 333.34, rounded half up per line
        self.assertEqual(figures.over_budget_by, D("0.02"))
        self.assertEqual((figures.spent, figures.budget_left), (D("0.00"), D("1000.00")))


class PurchaseTests(RestockTestCase):
    def setUp(self):
        super().setUp()
        self.p = self.plan(budget="5000")
        self.soap_line = services.add_line(self.p, user=self.user, product=self.soap, quantity="10", unit_cost="120")
        self.soda = services.add_line(self.p, user=self.user, description="Caustic soda", unit="kg", quantity="5",
                                      unit_cost="200")

    def buy(self, line, qty, paid, **kw):
        return services.record_purchase(line, user=self.user, quantity=qty, amount_paid=paid,
                                        purchase_date=timezone.localdate(), **kw)

    def test_product_purchase_goes_into_stock(self):
        purchase = self.buy(self.soap_line, "4", "520", reference="Supplier A")
        self.soap.refresh_from_db()
        self.assertEqual(self.soap.stock_qty, D("44000"))
        movement = purchase.stock_movement
        self.assertEqual((movement.kind, movement.quantity, movement.unit_cost, movement.entered_unit),
                         (StockMovement.Kind.STOCK_IN, D("4000"), D("130.00"), "L"))
        self.assertEqual(movement.reference, "Supplier A")
        f = next(x for x in services.plan_figures(self.p).lines if x.line == self.soap_line)
        self.assertEqual((f.bought, f.paid, f.status, f.remaining), (D("4"), D("520.00"), "partly", D("6")))
        self.assertEqual(f.cost_difference, D("40.00"))  # 4 L planned at 120 = 480; paid 520
        self.buy(self.soap_line, "7", "770")
        f = next(x for x in services.plan_figures(self.p).lines if x.line == self.soap_line)
        self.assertEqual((f.bought, f.status), (D("11"), "more"))

    def test_other_items_dont_touch_stock(self):
        before = StockMovement.objects.count()
        purchase = self.buy(self.soda, "5", "950")
        self.assertIsNone(purchase.stock_movement)
        self.assertEqual(StockMovement.objects.count(), before)
        figures = services.plan_figures(self.p)
        self.assertEqual((figures.spent, figures.budget_left), (D("950.00"), D("4050.00")))
        self.assertEqual(next(x for x in figures.lines if x.line == self.soda).status, "done")

    def test_purchase_rules(self):
        token = uuid.uuid4()
        self.buy(self.soda, "1", "200", submission_token=token)
        cases = [
            dict(qty="0", paid="10"), dict(qty="1", paid="-1"),
            dict(qty="1.2345", paid="1", line=self.soap_line),  # more than 3 decimal places
            dict(qty="1", paid="200", submission_token=token),  # same form twice
        ]
        for case in cases:
            line = case.pop("line", self.soda)
            with self.subTest(case=case), self.assertRaises(ValidationError):
                self.buy(line, case.pop("qty"), case.pop("paid"), **case)
        with self.assertRaises(ValidationError):
            services.record_purchase(self.soda, user=self.user, quantity="1", amount_paid="1",
                                     purchase_date=timezone.localdate() + datetime.timedelta(days=1))

    def test_reverse_takes_stock_back_and_keeps_the_record(self):
        purchase = self.buy(self.soap_line, "4", "520")
        with self.assertRaises(ValidationError):
            services.reverse_purchase(purchase, user=self.user, reason=" ")
        services.reverse_purchase(purchase, user=self.user, reason="Typed the wrong amount")
        purchase.refresh_from_db()
        self.soap.refresh_from_db()
        self.assertTrue(purchase.is_reversed)
        self.assertEqual(self.soap.stock_qty, D("40000"))
        self.assertEqual(purchase.reversal_movement.quantity, D("-4000"))
        self.assertEqual(services.plan_figures(self.p).spent, D("0.00"))
        with self.assertRaises(ValidationError):
            services.reverse_purchase(purchase, user=self.user, reason="again")

    def test_purchases_and_lines_with_purchases_are_kept(self):
        purchase = self.buy(self.soda, "1", "200")
        purchase.amount_paid = D("1")
        with self.assertRaises(ImmutableRecordError):
            purchase.save()
        with self.assertRaises(ImmutableRecordError):
            purchase.delete()
        with self.assertRaises(ValidationError):
            services.save_plan(self.p, user=self.user, budget=None, notes="",
                               lines=[{"line": self.soda, "remove": True}])
        self.assertTrue(RestockLine.objects.filter(pk=self.soda.pk).exists())


class RestockPageTests(RestockTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_full_flow(self):
        self.sell(30)
        r = self.client.post(reverse("restock:list"), {"month": "2026-12-01", "budget": "3000"})
        plan = RestockPlan.objects.get()
        self.assertRedirects(r, reverse("restock:detail", args=[plan.pk]))
        line = plan.lines.get()
        page = self.client.get(reverse("restock:detail", args=[plan.pk]))
        self.assertContains(page, "Sold about 10 L a month")
        self.assertContains(page, "dist/restock-plan.js")
        r = self.client.post(reverse("restock:detail", args=[plan.pk]),
                             {"budget": "3000", "notes": "", f"qty_{line.pk}": "8", f"cost_{line.pk}": "125"})
        self.assertRedirects(r, reverse("restock:detail", args=[plan.pk]))
        line.refresh_from_db()
        self.assertEqual((line.planned_qty, line.planned_unit_cost), (D("8"), D("125")))
        self.client.post(reverse("restock:line_add", args=[plan.pk]),
                         {"kind": "item", "description": "Fragrance", "unit": "L", "quantity": "2", "unit_cost": "900"})
        self.assertTrue(plan.lines.filter(description="Fragrance").exists())
        buy_url = reverse("restock:purchase", args=[plan.pk, line.pk])
        form = self.client.get(buy_url).context["form"]
        self.assertEqual((form.initial["quantity"], form.initial["amount_paid"]), (D("8"), D("1000.00")))
        data = {"quantity": "8", "amount_paid": "1040", "purchase_date": str(timezone.localdate()),
                "reference": "", "token": str(form.initial["token"])}
        self.assertRedirects(self.client.post(buy_url, data), reverse("restock:detail", args=[plan.pk]))
        self.client.post(buy_url, data)  # double tap: not saved twice
        self.assertEqual(RestockPurchase.objects.count(), 1)
        page = self.client.get(reverse("restock:detail", args=[plan.pk]))
        self.assertContains(page, "KES 40.00 more")
        purchase = RestockPurchase.objects.get()
        r = self.client.post(reverse("restock:reverse", args=[plan.pk, purchase.pk]), {"reason": "Wrong amount"})
        self.assertRedirects(r, reverse("restock:detail", args=[plan.pk]))
        purchase.refresh_from_db()
        self.assertTrue(purchase.is_reversed)

    def test_dashboard_shows_this_months_plan(self):
        r = self.client.get(reverse("core:dashboard"))
        self.assertContains(r, "Restock this month")
        self.assertContains(r, "No plan for")
        services.create_plan(month=timezone.localdate(), user=self.user, budget="1000")
        r = self.client.get(reverse("core:dashboard"))
        self.assertContains(r, "Budget left")
        self.assertContains(r, reverse("restock:list"))  # sidebar link

    def test_permissions(self):
        plan = services.create_plan(month=datetime.date(2026, 12, 1), user=self.user)
        viewer = make_staff("viewer", perms=("restock.view_restockplan",))
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("restock:detail", args=[plan.pk])).status_code, 200)
        self.assertNotContains(self.client.get(reverse("restock:detail", args=[plan.pk])), "Save plan")
        self.assertEqual(self.client.post(reverse("restock:line_add", args=[plan.pk]), {}).status_code, 403)
        line = services.add_line(plan, user=self.user, description="Labels", quantity="1")
        self.assertEqual(self.client.get(reverse("restock:purchase", args=[plan.pk, line.pk])).status_code, 403)
        self.client.force_login(make_staff("nobody"))
        self.assertEqual(self.client.get(reverse("restock:list")).status_code, 403)
