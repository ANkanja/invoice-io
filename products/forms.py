from decimal import Decimal

from django import forms
from django.conf import settings
from django.db.models import Q
from django.db.models.functions import Lower

from core.forms import BootstrapFormMixin
from core.validators import validate_image_upload

from .models import Category, Product, SellingOption, Unit
from .units import IncompatibleUnits, check_compatible, conversion_factor, display_unit_for

MONEY_ATTRS = {"inputmode": "decimal", "step": "0.01", "min": "0"}
QTY_ATTRS = {"inputmode": "decimal", "step": "any", "min": "0"}


class UnitChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, unit):
        if unit.dimension == Unit.Dimension.COUNT:
            return unit.plural
        return f"{unit.plural} ({unit.symbol})"


def stock_unit_queryset():
    """Stock is counted in a base unit (ml, g) or a count unit."""
    return Unit.objects.filter(is_active=True).filter(Q(dimension="count") | Q(factor=1)).order_by("dimension", "name")


def units_payload():
    """All active units, for the TypeScript that filters unit dropdowns."""
    return [
        {"id": u.pk, "code": u.code, "symbol": u.symbol, "name": u.name, "plural": u.plural,
         "dimension": u.dimension, "factor": f"{u.factor.normalize():f}"}
        for u in Unit.objects.filter(is_active=True)
    ]


def _pack_label(pack: Unit, qty: Decimal, unit: Unit) -> str:
    """'5 L container', '500 ml bottle', 'bar', 'carton of 24 pieces'."""
    from core.money import format_quantity

    if unit.dimension == "count":
        if pack == unit and qty == 1:
            return pack.name
        return f"{pack.name} of {format_quantity(qty)} {unit.name if qty == 1 else unit.plural}"
    return f"{format_quantity(qty)} {unit.symbol} {pack.name}"


class CategoryForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name"]
        labels = {"name": "Category name"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_bootstrap()

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())


class SellingOptionFields(BootstrapFormMixin, forms.Form):
    """How a product is sold: one pack at a price, or a price per measure.
    Used inside the new-product form and on its own (add/edit option)."""

    method = forms.ChoiceField(
        choices=SellingOption.Method.choices, initial=SellingOption.Method.PACKAGE, label="How is it sold?",
        widget=forms.RadioSelect(attrs={"data-option-method": ""}),
    )
    label = forms.CharField(max_length=60, required=False, label="Name on invoices",
                            help_text="Leave blank to use e.g. “5 L container” or “per litre”.")
    package_unit = UnitChoiceField(queryset=Unit.objects.filter(dimension="count", is_active=True), required=False,
                                   label="Each pack is a", empty_label=None)
    content_quantity = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=14, decimal_places=3,
                                          label="Each pack holds", widget=forms.NumberInput(attrs=QTY_ATTRS))
    content_unit = UnitChoiceField(queryset=Unit.objects.filter(is_active=True), required=False, empty_label=None,
                                   label="Unit", widget=forms.Select(attrs={"data-dimension-filter": ""}))
    price_unit = UnitChoiceField(queryset=Unit.objects.filter(is_active=True), required=False, empty_label=None,
                                 label="Price is per", widget=forms.Select(attrs={"data-dimension-filter": ""}))
    price = forms.DecimalField(min_value=Decimal("0"), max_digits=12, decimal_places=2, label="Selling price (KES)",
                               widget=forms.NumberInput(attrs=MONEY_ATTRS))
    institution_price = forms.DecimalField(
        required=False, min_value=Decimal("0"), max_digits=12, decimal_places=2, label="Institution price (KES)",
        help_text="Leave blank to charge institutions the selling price.", widget=forms.NumberInput(attrs=MONEY_ATTRS))
    cost_price = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=12, decimal_places=2,
                                    label="Cost price (KES)", help_text="Optional, per pack or per unit as priced.",
                                    widget=forms.NumberInput(attrs=MONEY_ATTRS))

    def clean_option(self, stock_unit: Unit | None) -> dict:
        """Validate against the product's stock unit; returns SellingOption field values."""
        d = self.cleaned_data
        method = d.get("method")
        if not stock_unit or method not in SellingOption.Method.values:
            return {}
        if method == SellingOption.Method.PACKAGE:
            pack, qty, unit = d.get("package_unit"), d.get("content_quantity"), d.get("content_unit")
            if not pack:
                self.add_error("package_unit", "Choose what one pack is called.")
            if not qty:
                self.add_error("content_quantity", "Say how much one pack holds.")
            if not pack or not qty:
                return {}
            unit = unit or stock_unit
            try:
                content = qty * conversion_factor(unit, stock_unit)
            except IncompatibleUnits as exc:
                self.add_error("content_unit", exc.messages[0])
                return {}
            if content != content.quantize(Decimal("0.001")):
                self.add_error("content_quantity", "Too precise for the stock unit.")
                return {}
            label = d.get("label") or _pack_label(pack, qty, unit)
            return {"method": method, "label": label, "package_unit": pack, "content_quantity": content,
                    "price_unit": None, "price": d["price"], "institution_price": d.get("institution_price"),
                    "cost_price": d.get("cost_price")}
        price_unit = d.get("price_unit")
        if not price_unit:
            self.add_error("price_unit", "Choose what the price is per.")
            return {}
        try:
            check_compatible(price_unit, stock_unit)
        except IncompatibleUnits as exc:
            self.add_error("price_unit", exc.messages[0])
            return {}
        return {"method": method, "label": d.get("label") or f"per {price_unit.name}", "package_unit": None,
                "content_quantity": None, "price_unit": price_unit, "price": d["price"],
                "institution_price": d.get("institution_price"), "cost_price": d.get("cost_price")}


class ProductForm(BootstrapFormMixin, forms.ModelForm):
    photo = forms.ImageField(
        required=False, validators=[validate_image_upload],
        widget=forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp", "data-image-preview": "photo-preview"}),
        help_text="PNG, JPEG or WebP. Large phone photos are resized automatically.",
    )
    remove_photo = forms.BooleanField(required=False, label="Remove the current photo")
    stock_unit = UnitChoiceField(queryset=stock_unit_queryset(), empty_label=None, label="Count stock in",
                                 widget=forms.Select(attrs={"data-stock-unit": ""}),
                                 help_text="Liquids: millilitres. Powders: grams. Things counted one by one: pieces, bottles…")
    low_stock_alert = forms.DecimalField(
        required=False, min_value=Decimal("0"), max_digits=14, decimal_places=3, label="Low-stock alert at",
        widget=forms.NumberInput(attrs=QTY_ATTRS),
        help_text="Leave blank for no alert.",
    )
    opening_stock = forms.DecimalField(
        required=False, min_value=Decimal("0"), max_digits=14, decimal_places=3, label="Opening stock",
        widget=forms.NumberInput(attrs=QTY_ATTRS), help_text="How much you have right now.",
    )
    opening_stock_unit = UnitChoiceField(queryset=Unit.objects.filter(is_active=True), required=False, empty_label=None,
                                         label="Unit", widget=forms.Select(attrs={"data-dimension-filter": ""}))

    class Meta:
        model = Product
        fields = ["name", "category", "sku", "description", "stock_unit", "track_stock"]
        labels = {"track_stock": "Track stock for this product"}
        help_texts = {
            "sku": "Optional internal code.",
            "track_stock": "Untick for things you don't hold stock of, such as delivery.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "track_stock": forms.CheckboxInput(attrs={"data-controls": "stock-fields"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_new = self.instance.pk is None
        categories = Category.objects.filter(is_active=True)
        if self.instance.category_id:
            categories = categories | Category.objects.filter(pk=self.instance.category_id)
        self.fields["category"].queryset = categories.distinct().order_by("name")
        self.fields["category"].empty_label = "No category"
        self.fields["photo"].widget.attrs["data-max-mb"] = str(settings.MAX_UPLOAD_MB)

        if self.is_new:
            self.option = SellingOptionFields(*args, prefix="option", **{k: v for k, v in kwargs.items() if k == "initial"})
            self.option.fields["content_unit"].widget.attrs["class"] = "form-select"
        else:
            self.option = None
            del self.fields["opening_stock"], self.fields["opening_stock_unit"]
            if not self.instance.photo:
                del self.fields["remove_photo"]
            # Changing the stock unit after stock or sales exist would scramble the history.
            if self.instance.stock_movements.exists() or self.instance.invoice_lines.exists():
                self.fields["stock_unit"].disabled = True
                self.fields["stock_unit"].help_text = "Fixed, because this product already has stock history or sales."
            if self.instance.low_stock_threshold is not None:
                display = display_unit_for(self.instance.stock_unit)
                self.initial["low_stock_alert"] = (
                    self.instance.low_stock_threshold * conversion_factor(self.instance.stock_unit, display)
                ).normalize()
        self._apply_bootstrap()
        if self.option is not None:
            self.option._apply_bootstrap()
            self.option.fields["method"].widget.attrs["class"] = "form-check-input"

    def alert_unit(self, stock_unit=None):
        stock_unit = stock_unit or getattr(self, "cleaned_data", {}).get("stock_unit")
        if stock_unit is None and self.instance.stock_unit_id:
            stock_unit = self.instance.stock_unit
        return display_unit_for(stock_unit) if stock_unit is not None else None

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        clash = Product.objects.annotate(n=Lower("name")).filter(n=name.lower()).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(
                "You already have a product with this name. Add another way to sell it (e.g. a different bottle size) "
                "on that product's page instead, so they share one stock."
            )
        return name

    def clean_sku(self):
        sku = (self.cleaned_data.get("sku") or "").strip().upper()
        return sku or None

    def is_valid(self):
        valid = super().is_valid()
        if self.option is not None:
            valid = self.option.is_valid() and valid
        return valid

    def clean(self):
        data = super().clean()
        stock_unit = data.get("stock_unit") or (self.instance.stock_unit if not self.is_new else None)
        if not data.get("track_stock"):
            data["low_stock_alert"] = None
            if data.get("opening_stock"):
                self.add_error("opening_stock", "Tick “Track stock” to record opening stock.")
        if stock_unit is not None:
            alert = data.get("low_stock_alert")
            data["low_stock_threshold"] = None if alert is None else alert * conversion_factor(self.alert_unit(stock_unit), stock_unit)
            opening, unit = data.get("opening_stock"), data.get("opening_stock_unit") or stock_unit
            if opening:
                try:
                    data["opening_stock_base"] = opening * conversion_factor(unit, stock_unit)
                    data["opening_stock_label"] = unit.plural if unit.dimension == "count" else unit.symbol
                except IncompatibleUnits as exc:
                    self.add_error("opening_stock_unit", exc.messages[0])
            if self.option is not None and self.option.is_valid():
                self.option_values = self.option.clean_option(stock_unit)
        if data.get("photo") and data.get("remove_photo"):
            self.add_error("remove_photo", "Either upload a new photo or remove the current one, not both.")
        return data

    def save(self, commit=True):
        product = super().save(commit=False)
        product.low_stock_threshold = self.cleaned_data.get("low_stock_threshold")
        if commit:
            product.save()
        return product


class SellingOptionForm(SellingOptionFields):
    is_default = forms.BooleanField(required=False, label="Use by default on invoices")
    is_active = forms.BooleanField(required=False, initial=True, label="Available for new invoices")

    def __init__(self, *args, product: Product, instance: SellingOption | None = None, **kwargs):
        self.product, self.instance = product, instance
        if instance is not None and "initial" not in kwargs:
            initial = {"method": instance.method, "label": instance.label, "price": instance.price,
                       "institution_price": instance.institution_price, "cost_price": instance.cost_price, "is_default": instance.is_default, "is_active": instance.is_active}
            if instance.method == SellingOption.Method.PACKAGE:
                display = display_unit_for(product.stock_unit) if product.stock_unit.dimension != "count" else product.stock_unit
                initial.update(package_unit=instance.package_unit,
                               content_quantity=(instance.content_quantity * conversion_factor(product.stock_unit, display)).normalize(),
                               content_unit=display)
            else:
                initial.update(price_unit=instance.price_unit)
            kwargs["initial"] = initial
        super().__init__(*args, **kwargs)
        self._apply_bootstrap()
        self.fields["method"].widget.attrs["class"] = "form-check-input"
        dim_units = Unit.objects.filter(is_active=True, dimension=product.stock_unit.dimension)
        if product.stock_unit.dimension == "count":
            dim_units = dim_units.filter(pk=product.stock_unit_id)
        self.fields["content_unit"].queryset = dim_units
        self.fields["price_unit"].queryset = dim_units
        if not self.is_bound and instance is None:
            self.initial.setdefault("content_unit", display_unit_for(product.stock_unit))
            self.initial.setdefault("price_unit", display_unit_for(product.stock_unit))
            self.initial.setdefault("is_default", not product.options.filter(is_active=True).exists())

    def clean(self):
        data = super().clean()
        self.option_values = self.clean_option(self.product.stock_unit) if not self.errors else {}
        return data


class ProductFilterForm(forms.Form):
    """Search and filters on the product list (GET)."""

    STATUS_CHOICES = [("active", "Active"), ("archived", "Archived"), ("all", "All")]
    STOCK_CHOICES = [("", "Any stock level"), ("low", "Low stock"), ("out", "Out of stock")]

    q = forms.CharField(required=False, label="Search",
                        widget=forms.TextInput(attrs={"placeholder": "Name, SKU or size", "type": "search"}))
    category = forms.ChoiceField(required=False)
    status = forms.ChoiceField(required=False, choices=STATUS_CHOICES)
    stock = forms.ChoiceField(required=False, choices=STOCK_CHOICES, label="Stock")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].choices = (
            [("", "All categories"), ("none", "No category")]
            + [(str(c.pk), c.name) for c in Category.objects.order_by("name")]
        )
        for name, field in self.fields.items():
            css = "form-select" if isinstance(field, forms.ChoiceField) else "form-control"
            field.widget.attrs.setdefault("class", css)
            if isinstance(field, forms.ChoiceField):
                field.widget.attrs["data-auto-submit"] = ""
