"""Small helpers for building test data. Values are obviously fictional test
fixtures, never real business records."""

from decimal import Decimal

from django.contrib.auth import get_user_model

from core.models import BusinessSettings
from customers.models import Customer
from products.models import Product

TEST_PASSWORD = "test-pass-Long-123"


def make_user(username="owner", **kwargs):
    """The business owner: a superuser, like the account made with createsuperuser."""
    User = get_user_model()
    if User.objects.filter(username=username).exists():
        return User.objects.get(username=username)
    return User.objects.create_superuser(username=username, password=TEST_PASSWORD, email="", **kwargs)


def make_staff(username="staff", perms: tuple[str, ...] = ()):
    """A normal signed-in user with only the given permissions ("app.codename")."""
    from django.contrib.auth.models import Permission

    user = get_user_model().objects.create_user(username=username, password=TEST_PASSWORD)
    for perm in perms:
        app_label, codename = perm.split(".")
        user.user_permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))
    return get_user_model().objects.get(pk=user.pk)  # fresh copy: permission cache cleared


def configure_business(**overrides) -> BusinessSettings:
    s = BusinessSettings.load()
    s.name = overrides.pop("name", "Test Business")
    s.phone = overrides.pop("phone", "0700 000000")
    for key, value in overrides.items():
        setattr(s, key, value)
    s.save()
    return s


def unit(code: str):
    """A Unit by code ('ml', 'l', 'g', 'kg', 'bottle'…); unknown count units are created."""
    from django.utils.text import slugify

    from products.models import Unit

    code = slugify(code) or "piece"
    found = Unit.objects.filter(code=code).first()
    if found:
        return found
    return Unit.objects.create(code=code, name=code, plural=f"{code}s", symbol=code, dimension="count")


def make_product(name="Test Soap", price="100.00", unit="bottle", package_size="500 ml", *, stock_unit=None,
                 cost_price=None, **kwargs) -> Product:
    """A product counted one by one in `unit`, sold as one pack option at `price`
    (labelled e.g. "500 ml bottle"). For products stocked in ml/g pass
    stock_unit="ml" and add options with make_option()."""
    from products.models import SellingOption

    count_unit = globals()["unit"](unit)
    product = Product.objects.create(name=name, stock_unit=globals()["unit"](stock_unit) if stock_unit else count_unit,
                                     **kwargs)
    if stock_unit is None:
        SellingOption.objects.create(
            product=product, method="package", label=f"{package_size} {unit}".strip() if package_size else unit,
            package_unit=count_unit, content_quantity=Decimal("1"), price=Decimal(price),
            cost_price=Decimal(cost_price) if cost_price is not None else None, is_default=True,
        )
    return product


def make_option(product, *, price, label="", pack=None, content=None, per=None, default=False, cost_price=None):
    """Add a selling option: make_option(p, pack="bottle", content="500", price="140")
    or make_option(p, per="l", price="250")."""
    from products.models import SellingOption

    if per:
        u = unit(per)
        return SellingOption.objects.create(product=product, method="measure", label=label or f"per {u.name}",
                                            price_unit=u, price=Decimal(price), is_default=default,
                                            cost_price=cost_price)
    pack_unit = unit(pack or "piece")
    return SellingOption.objects.create(product=product, method="package", label=label or pack_unit.name,
                                        package_unit=pack_unit, content_quantity=Decimal(content or "1"),
                                        price=Decimal(price), is_default=default, cost_price=cost_price)


def set_price(product, price) -> None:
    """Change the product's default selling price (as editing it on screen would)."""
    option = product.default_option
    option.price = Decimal(price)
    option.save()


def make_customer(name="Test Customer", **kwargs) -> Customer:
    return Customer.objects.create(name=name, **kwargs)
