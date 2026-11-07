import uuid
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from core.testing import configure_business, make_option, make_product, make_staff, make_user
from inventory.models import StockMovement
from inventory.services import find_stock_discrepancies, record_stock_in


class StockViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business()
        self.client.force_login(self.user)
        self.soap = make_product("Bar soap", "80", package_size="200 g", low_stock_threshold=D("10"), cost_price=D("50"))
        self.bleach = make_product("Bleach", "120", package_size="1 L")

    def qty(self, p):
        p.refresh_from_db()
        return p.stock_qty

    def post(self, name, **data):
        data.setdefault("token", str(uuid.uuid4()))
        return self.client.post(reverse(f"inventory:{name}"), data)

    # --- stock in --------------------------------------------------------------
    def test_stock_in_form_prefills_product_and_unit(self):
        r = self.client.get(reverse("inventory:stock_in"), {"product": self.soap.pk})
        form = r.context["form"]
        self.assertEqual(form.initial["product"], self.soap)
        self.assertEqual(form.initial["measure"], "unit:bottle")
        self.assertIn(str(self.soap.pk), r.context["product_data"]["products"])

    def test_liquid_stock_in_litres_ml_or_containers(self):
        soap = make_product("Liquid soap", stock_unit="ml")
        container = make_option(soap, pack="container", content="5000", price="500", label="5 L container", default=True)
        form = self.client.get(reverse("inventory:stock_in"), {"product": soap.pk}).context["form"]
        self.assertEqual(form.initial["measure"], "unit:l")  # friendly unit first
        self.post("stock_in", product=soap.pk, quantity="20", measure="unit:l", unit_cost="80")
        self.post("stock_in", product=soap.pk, quantity="500", measure="unit:ml")
        r = self.post("stock_in", product=soap.pk, quantity="4", measure=f"option:{container.pk}")
        self.assertEqual(self.qty(soap), D("40500"))  # 20 L + 500 ml + 4 × 5 L, in ml
        latest = StockMovement.objects.filter(product=soap).order_by("pk")
        self.assertEqual([(m.entered_quantity, m.entered_unit) for m in latest],
                         [(D("20"), "litres"), (D("500"), "millilitres"), (D("4"), "5 L container")])
        self.assertEqual(latest[0].unit_cost, D("80.00"))  # per litre, as entered
        r = self.client.get(reverse("products:detail", args=[soap.pk]))
        self.assertContains(r, "40.5 L")
        # A unit of another kind is refused.
        r = self.post("stock_in", product=soap.pk, quantity="1", measure="unit:kg")
        self.assertIn("measure", r.context["form"].errors)
        self.assertEqual(self.qty(soap), D("40500"))

    def test_customer_return(self):
        record_stock_in(self.soap, "5", self.user)
        r = self.post("return", product=self.soap.pk, quantity="2", reference="INV-2026-0001", notes="Unopened")
        self.assertRedirects(r, reverse("products:detail", args=[self.soap.pk]))
        m = StockMovement.objects.latest("pk")
        self.assertEqual((m.kind, m.quantity, self.qty(self.soap)), ("return", D("2"), D("7")))

    def test_stock_in(self):
        r = self.post("stock_in", product=self.soap.pk, quantity="24", unit_cost="48", reference="Delivery 7")
        self.assertRedirects(r, reverse("products:detail", args=[self.soap.pk]))
        self.assertEqual(self.qty(self.soap), D("24"))
        m = StockMovement.objects.get()
        self.assertEqual((m.reference, m.unit_cost, m.created_by), ("Delivery 7", D("48.00"), self.user))

    def test_same_form_submitted_twice_records_once(self):
        token = str(uuid.uuid4())
        self.post("stock_in", product=self.soap.pk, quantity="5", token=token)
        r = self.client.post(reverse("inventory:stock_in"), {"product": self.soap.pk, "quantity": "5", "token": token}, follow=True)
        self.assertContains(r, "already recorded")
        self.assertEqual(self.qty(self.soap), D("5"))
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_save_and_record_another(self):
        r = self.post("stock_in", product=self.soap.pk, quantity="1", add_another="1")
        self.assertRedirects(r, reverse("inventory:stock_in"))

    def test_invalid_quantities(self):
        for bad in ["0", "-3", "1.2345", "abc"]:
            with self.subTest(bad=bad):
                r = self.post("stock_in", product=self.soap.pk, quantity=bad)
                self.assertEqual(r.status_code, 200)
        self.assertFalse(StockMovement.objects.exists())

    def test_archived_or_untracked_products_cannot_be_chosen(self):
        archived = make_product("Old", package_size="", is_active=False)
        untracked = make_product("Delivery", package_size="", track_stock=False)
        for p in (archived, untracked):
            r = self.post("stock_in", product=p.pk, quantity="1")
            self.assertIn("product", r.context["form"].errors)
        self.assertFalse(StockMovement.objects.exists())

    # --- stock out -------------------------------------------------------------
    def test_stock_out_requires_reason_and_enough_stock(self):
        record_stock_in(self.soap, "3", self.user)
        r = self.post("stock_out", product=self.soap.pk, quantity="1", reason="")
        self.assertIn("reason", r.context["form"].errors)
        r = self.post("stock_out", product=self.soap.pk, quantity="4", reason="damaged")
        self.assertContains(r, "Only 3 bottles of Bar soap in stock, so 4 bottles can&#x27;t be removed.")
        r = self.post("stock_out", product=self.soap.pk, quantity="2", reason="damaged", notes="Wet")
        self.assertRedirects(r, reverse("products:detail", args=[self.soap.pk]))
        self.assertEqual(self.qty(self.soap), D("1"))

    # --- counts ----------------------------------------------------------------
    def test_single_count(self):
        record_stock_in(self.soap, "10", self.user)
        self.post("count", product=self.soap.pk, counted_quantity="8")
        self.assertEqual(self.qty(self.soap), D("8"))
        r = self.client.post(reverse("inventory:count"), {"product": self.soap.pk, "counted_quantity": "8",
                                                          "token": str(uuid.uuid4())}, follow=True)
        self.assertContains(r, "Nothing to adjust")
        self.assertEqual(StockMovement.objects.filter(kind="adjustment").count(), 1)

    def test_stock_take(self):
        record_stock_in(self.soap, "10", self.user)
        record_stock_in(self.bleach, "5", self.user)
        third = make_product("Detergent", package_size="1 kg")
        token = str(uuid.uuid4())
        data = {"token": token, "notes": "Month end", f"count_{self.soap.pk}": "7",
                f"count_{self.bleach.pk}": "5", f"count_{third.pk}": ""}
        r = self.client.post(reverse("inventory:take"), data, follow=True)
        self.assertContains(r, "2 counted, 1 adjusted, 1 already correct")
        self.assertEqual((self.qty(self.soap), self.qty(self.bleach), self.qty(third)), (D("7"), D("5"), D("0")))
        # Submitting the very same stock take again changes nothing.
        r = self.client.post(reverse("inventory:take"), data, follow=True)
        self.assertContains(r, "already saved")
        self.assertEqual(StockMovement.objects.filter(kind="adjustment").count(), 1)
        self.assertEqual(find_stock_discrepancies(), [])

    def test_stock_take_counts_liquids_in_litres(self):
        soap = make_product("Liquid soap", stock_unit="ml")
        record_stock_in(soap, "20000", self.user)
        r = self.client.get(reverse("inventory:take"))
        self.assertContains(r, 'data-expected="20"')
        self.client.post(reverse("inventory:take"), {"token": str(uuid.uuid4()), f"count_{soap.pk}": "18.5"})
        self.assertEqual(self.qty(soap), D("18500"))
        m = StockMovement.objects.get(kind="adjustment")
        self.assertEqual((m.quantity, m.entered_quantity, m.entered_unit), (D("-1500"), D("18.5"), "L"))

    def test_stock_take_needs_a_count_and_rejects_bad_values_without_partial_saves(self):
        r = self.client.post(reverse("inventory:take"), {"token": str(uuid.uuid4())})
        self.assertContains(r, "Enter at least one counted quantity.")
        r = self.client.post(reverse("inventory:take"), {"token": str(uuid.uuid4()),
                                                         f"count_{self.soap.pk}": "4", f"count_{self.bleach.pk}": "-1"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(StockMovement.objects.exists())

    # --- overview & history ---------------------------------------------------
    def test_overview_filters(self):
        record_stock_in(self.soap, "4", self.user)       # low (≤10)
        url = reverse("inventory:overview")

        def names(**params):
            return [p.name for p in self.client.get(url, params).context["page"].object_list]

        self.assertEqual(names(), ["Bar soap", "Bleach"])
        self.assertEqual(names(level="low"), ["Bar soap"])
        self.assertEqual(names(level="out"), ["Bleach"])
        self.assertEqual(names(q="ble"), ["Bleach"])

    def test_history_filters(self):
        record_stock_in(self.soap, "4", self.user, reference="A")
        record_stock_in(self.bleach, "2", self.user, reference="B")
        url = reverse("inventory:movements")
        r = self.client.get(url, {"product": self.bleach.pk})
        self.assertEqual([m.reference for m in r.context["page"].object_list], ["B"])
        r = self.client.get(url, {"kind": "stock_out"})
        self.assertEqual(list(r.context["page"].object_list), [])
        self.assertContains(r, "No movements match")

    def test_sidebar_shows_attention_count(self):
        record_stock_in(self.soap, "4", self.user)       # low; bleach is out → 2
        r = self.client.get(reverse("core:dashboard"))
        self.assertEqual(r.context["low_stock_count"], 2)

    def test_permissions(self):
        self.client.force_login(make_staff(perms=("inventory.view_stockmovement", "products.view_product")))
        self.assertEqual(self.client.get(reverse("inventory:overview")).status_code, 200)
        self.assertEqual(self.client.get(reverse("inventory:movements")).status_code, 200)
        self.assertEqual(self.post("stock_in", product=self.soap.pk, quantity="1").status_code, 403)
        self.assertEqual(self.client.get(reverse("inventory:take")).status_code, 403)
