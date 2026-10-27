from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from core.testing import configure_business, make_customer, make_product, make_user
from invoices import services
from payments.models import Payment, PaymentMethod
from products.models import Category, Product


class ProductModelTests(TestCase):
    def test_create_and_update(self):
        p = make_product("Floor Cleaner", "350.00", package_size="1 L", unit="bottle")
        p.default_price = D("375.00")
        p.save()
        p.refresh_from_db()
        self.assertEqual(p.default_price, D("375.00"))
        self.assertEqual(str(p), "Floor Cleaner — 1 L")

    def test_negative_price_rejected(self):
        p = Product(name="X", unit="piece", default_price=D("-1"))
        with self.assertRaises(ValidationError):
            p.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            p.save()

    def test_duplicate_name_and_size_rejected_case_insensitive(self):
        make_product("Bleach", package_size="1 L")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_product("bleach", package_size="1 l")
        make_product("Bleach", package_size="5 L")  # different size is fine

    def test_blank_skus_allowed_many_times(self):
        make_product("A", sku="")
        make_product("B", sku="")
        self.assertEqual(Product.objects.filter(sku__isnull=True).count(), 2)

    def test_used_product_and_category_cannot_be_deleted(self):
        configure_business()
        user = make_user()
        cat = Category.objects.create(name="Soaps")
        p = make_product("Bar Soap", category=cat, track_stock=False)
        inv = services.create_draft(user=user, customer_name="X")
        services.add_line(inv, product=p, quantity="1")
        with self.assertRaises(ProtectedError):
            p.delete()
        with self.assertRaises(ProtectedError):
            cat.delete()


class CustomerAndPaymentModelTests(TestCase):
    def test_customer_phone_validation(self):
        c = make_customer("Test Hospital", kind="organization", phone="not a phone!")
        with self.assertRaises(ValidationError):
            c.full_clean()

    def test_payment_methods_seeded(self):
        self.assertEqual(
            list(PaymentMethod.objects.values_list("name", flat=True)), ["Cash", "M-Pesa", "Bank transfer", "Cheque"]
        )

    def test_duplicate_mpesa_reference_rejected(self):
        user = make_user()
        mpesa = PaymentMethod.objects.get(name="M-Pesa")
        Payment.objects.create(payer_name="A", amount=D("100"), method=mpesa, reference="sfk12abc", created_by=user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(payer_name="A", amount=D("100"), method=mpesa, reference=" SFK12ABC ", created_by=user)

    def test_payment_amount_cannot_be_edited(self):
        from core.models import ImmutableRecordError
        user = make_user()
        p = Payment.objects.create(payer_name="A", amount=D("100"), method=PaymentMethod.objects.first(), created_by=user)
        p.amount = D("1")
        with self.assertRaises(ImmutableRecordError):
            p.save()
        with self.assertRaises(ImmutableRecordError):
            p.delete()
