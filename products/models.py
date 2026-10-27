from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower

from core.models import TimeStampedModel
from core.validators import product_photo_path, validate_image_upload

NON_NEGATIVE = [MinValueValidator(Decimal("0"))]


class Category(TimeStampedModel):
    name = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"
        constraints = [models.UniqueConstraint(Lower("name"), name="category_name_unique_ci")]

    def __str__(self):
        return self.name


class ProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def low_stock(self):
        """Active, stock-tracked products at or below their alert threshold."""
        return self.filter(
            is_active=True,
            track_stock=True,
            low_stock_threshold__isnull=False,
            stock_qty__lte=F("low_stock_threshold"),
        )


class Product(TimeStampedModel):
    """One sellable item at one size, e.g. 'Liquid hand soap — 5 L'.

    Prices entered here are defaults only. Invoice lines keep their own copy of
    the price actually charged, so editing a product never changes old invoices.
    Products are archived (is_active=False), never deleted once used.
    """

    name = models.CharField(max_length=120)
    sku = models.CharField("SKU", max_length=40, unique=True, null=True, blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, null=True, blank=True, related_name="products"
    )
    description = models.TextField(blank=True)
    unit = models.CharField(max_length=30, help_text="Unit of sale, e.g. bottle, litre, container, piece.")
    package_size = models.CharField(max_length=40, blank=True, help_text="E.g. 500 ml, 5 L, 20 L.")
    default_price = models.DecimalField(max_digits=12, decimal_places=2, validators=NON_NEGATIVE)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=NON_NEGATIVE)
    photo = models.ImageField(upload_to=product_photo_path, blank=True, validators=[validate_image_upload])

    # Stock. stock_qty is a cached running total of inventory.StockMovement and is
    # only ever changed by inventory.services — never edited directly.
    track_stock = models.BooleanField(default=True)
    stock_qty = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"), editable=False)
    low_stock_threshold = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, validators=NON_NEGATIVE,
        help_text="Show a low-stock alert at or below this quantity. Leave blank for no alert.",
    )

    is_active = models.BooleanField(default=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["name", "package_size"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["category", "is_active"]),
        ]
        constraints = [
            models.UniqueConstraint(Lower("name"), Lower("package_size"), name="product_name_size_unique_ci"),
            models.CheckConstraint(condition=Q(default_price__gte=0), name="product_default_price_gte_0"),
            models.CheckConstraint(
                condition=Q(cost_price__isnull=True) | Q(cost_price__gte=0), name="product_cost_price_gte_0"
            ),
            models.CheckConstraint(
                condition=Q(low_stock_threshold__isnull=True) | Q(low_stock_threshold__gte=0),
                name="product_low_stock_threshold_gte_0",
            ),
        ]

    def __str__(self):
        return f"{self.name} — {self.package_size}" if self.package_size else self.name

    def save(self, *args, **kwargs):
        if self.sku == "":
            self.sku = None  # keep the unique constraint happy for blank SKUs
        if self._state.adding:
            # New products start at zero; opening stock is recorded as a stock-in.
            self.stock_qty = Decimal("0.00")
        elif kwargs.get("update_fields") is None:
            # Never write stock_qty from a (possibly stale) in-memory copy.
            # Only inventory.services changes it, via an F() update.
            kwargs["update_fields"] = [
                f.name for f in self._meta.concrete_fields if not f.primary_key and f.name != "stock_qty"
            ]
        super().save(*args, **kwargs)

    @property
    def is_low_stock(self) -> bool:
        return (
            self.track_stock
            and self.low_stock_threshold is not None
            and self.stock_qty <= self.low_stock_threshold
        )
