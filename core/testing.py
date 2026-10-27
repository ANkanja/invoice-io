"""Small helpers for building test data. Values are obviously fictional test
fixtures, never real business records."""

from decimal import Decimal

from django.contrib.auth import get_user_model

from core.models import BusinessSettings
from customers.models import Customer
from products.models import Product

TEST_PASSWORD = "test-pass-Long-123"


def make_user(username="owner", **kwargs):
    return get_user_model().objects.create_user(username=username, password=TEST_PASSWORD, **kwargs)


def configure_business(**overrides) -> BusinessSettings:
    s = BusinessSettings.load()
    s.name = overrides.pop("name", "Test Business")
    s.phone = overrides.pop("phone", "0700 000000")
    for key, value in overrides.items():
        setattr(s, key, value)
    s.save()
    return s


def make_product(name="Test Soap", price="100.00", **kwargs) -> Product:
    kwargs.setdefault("unit", "bottle")
    kwargs.setdefault("package_size", "500 ml")
    return Product.objects.create(name=name, default_price=Decimal(price), **kwargs)


def make_customer(name="Test Customer", **kwargs) -> Customer:
    return Customer.objects.create(name=name, **kwargs)
