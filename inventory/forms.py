import uuid
from decimal import Decimal

from django import forms

from core.forms import BootstrapFormMixin
from products.models import Category, Product
from products.pricing import stock_entry_choices, to_stock_units
from products.units import IncompatibleUnits, conversion_factor, display_unit_for

from .models import StockMovement

QTY = {"max_digits": 12, "decimal_places": 3}
QTY_ATTRS = {"inputmode": "decimal", "step": "any", "min": "0", "autocomplete": "off"}


def stock_products():
    """Products that can have stock recorded: active and stock-tracked."""
    return (Product.objects.filter(is_active=True, track_stock=True).select_related("category", "stock_unit")
            .prefetch_related("options").order_by("name"))


def default_measure(product) -> str:
    """Stock forms start in the friendly unit: litres for ml, kg for g."""
    return f"unit:{display_unit_for(product.stock_unit).code}"


class StockProductField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj} — {obj.stock_display} in stock"


class StockMovementForm(BootstrapFormMixin, forms.Form):
    """Shared parts: which product, the quantity and what it's counted in
    (litres, ml, or one of the product's packs such as "5 L container"), and a
    one-time token that stops the same form being recorded twice.

    cleaned_data gains `base_quantity` (in the product's stock unit) and
    `entered` = (quantity, unit label) as typed, for the history.
    """

    quantity_field = "quantity"

    product = StockProductField(queryset=stock_products(), empty_label="Choose a product…")
    measure = forms.ChoiceField(label="Counted in", choices=[], required=False)
    token = forms.UUIDField(widget=forms.HiddenInput)

    field_order = ["product", "quantity", "counted_quantity", "measure"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = stock_products()
        self.fields["product"].widget.attrs["data-stock-product"] = ""
        self.fields["measure"].widget.attrs["data-stock-measure"] = ""
        if not self.is_bound:
            self.initial.setdefault("token", uuid.uuid4())
        product = self._chosen_product()
        if product is not None:
            self.fields["measure"].choices = [(value, label) for value, label, _f in stock_entry_choices(product)]
            if not self.is_bound:
                self.initial.setdefault("measure", default_measure(product))
        self._apply_bootstrap()

    def _chosen_product(self):
        raw = self.data.get(self.add_prefix("product")) if self.is_bound else self.initial.get("product")
        if isinstance(raw, Product):
            return raw
        if raw and str(raw).isdigit():
            return stock_products().filter(pk=int(raw)).first()
        return None

    def quantity_fields(self):
        return self[self.quantity_field], self["measure"]

    def other_fields(self):
        skip = {"product", "token", "measure", self.quantity_field}
        return [f for f in self if f.name not in skip]

    def clean(self):
        data = super().clean()
        product, qty = data.get("product"), data.get(self.quantity_field)
        if product is None or qty is None:
            return data
        choice = data.get("measure") or default_measure(product)
        try:
            base, label = to_stock_units(product, qty, choice)
        except IncompatibleUnits as exc:
            self.add_error("measure", exc.messages[0])
            return data
        except forms.ValidationError as exc:
            self.add_error(self.quantity_field, exc.messages[0])
            return data
        data["base_quantity"] = base
        data["entered"] = (qty, label)
        data["measure_label"] = label
        return data


class StockInForm(StockMovementForm):
    quantity = forms.DecimalField(min_value=Decimal("0.001"), **QTY, label="Quantity received",
                                  widget=forms.NumberInput(attrs=QTY_ATTRS))
    unit_cost = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=12, decimal_places=2,
                                   label="Cost each (KES)", help_text="Optional. Per litre, container… whatever you counted in above.",
                                   widget=forms.NumberInput(attrs={**QTY_ATTRS, "data-unit-cost": ""}))
    reference = forms.CharField(required=False, max_length=80, label="Reference",
                                help_text="Optional, e.g. supplier invoice or batch number, or “Opening stock”.")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class StockOutForm(StockMovementForm):
    quantity = forms.DecimalField(min_value=Decimal("0.001"), **QTY, label="Quantity removed",
                                  widget=forms.NumberInput(attrs={**QTY_ATTRS, "data-stock-out": ""}))
    reason = forms.ChoiceField(choices=[("", "Choose a reason…")] + list(StockMovement.OutReason.choices))
    reference = forms.CharField(required=False, max_length=80)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["notes"].help_text = "Say what happened, e.g. “3 bottles leaked in transport”."


class ReturnForm(StockMovementForm):
    quantity = forms.DecimalField(min_value=Decimal("0.001"), **QTY, label="Quantity returned",
                                  widget=forms.NumberInput(attrs=QTY_ATTRS))
    reference = forms.CharField(required=False, max_length=80, label="Invoice number",
                                help_text="Optional: the invoice the goods were sold on.")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                            help_text="E.g. “Customer returned 2 unopened containers”. Refunds are recorded with payments.")


class StockCountForm(StockMovementForm):
    quantity_field = "counted_quantity"
    counted_quantity = forms.DecimalField(min_value=Decimal("0"), **QTY, label="Quantity counted",
                                          help_text="What is physically there now. The difference is recorded as an adjustment.",
                                          widget=forms.NumberInput(attrs={**QTY_ATTRS, "data-stock-count": ""}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class StockTakeForm(forms.Form):
    """Whole-shop count: one optional 'counted' box per product, in the product's
    friendly unit (litres for liquids kept in ml). Blank = not counted."""

    token = forms.UUIDField(widget=forms.HiddenInput)
    notes = forms.CharField(required=False, max_length=200, label="Note for this stock take",
                            widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. End of month count"}))

    def __init__(self, *args, products, **kwargs):
        super().__init__(*args, **kwargs)
        self.products = list(products)
        if not self.is_bound:
            self.initial.setdefault("token", uuid.uuid4())
        self.display = {}
        for p in self.products:
            unit = display_unit_for(p.stock_unit)
            factor = conversion_factor(unit, p.stock_unit)  # display unit → stock unit
            self.display[p.pk] = (unit, factor)
            expected = (p.stock_qty / factor).normalize()
            self.fields[f"count_{p.pk}"] = forms.DecimalField(
                required=False, min_value=Decimal("0"), **QTY, label=str(p),
                widget=forms.NumberInput(attrs={**QTY_ATTRS, "class": "form-control form-control-sm count-input",
                                                "data-expected": f"{expected:f}"}),
            )

    def rows(self):
        for p in self.products:
            unit = self.display[p.pk][0]
            yield p, self[f"count_{p.pk}"], unit.plural if unit.dimension == "count" else unit.symbol

    def counts(self) -> dict[int, tuple[Decimal, tuple]]:
        """{product id: (count in stock units, (count as typed, unit))}."""
        result = {}
        for p in self.products:
            value = self.cleaned_data.get(f"count_{p.pk}")
            if value is None:
                continue
            unit, factor = self.display[p.pk]
            base = value * factor
            if base != base.quantize(Decimal("0.001")):
                continue  # rejected in clean()
            label = unit.plural if unit.dimension == "count" else unit.symbol
            result[p.pk] = (base, (value, label))
        return result

    def clean(self):
        data = super().clean()
        for p in self.products:
            value = data.get(f"count_{p.pk}")
            if value is not None:
                base = value * self.display[p.pk][1]
                if base != base.quantize(Decimal("0.001")):
                    self.add_error(f"count_{p.pk}", "Too precise; use fewer decimal places.")
        if not self.errors and not self.counts():
            raise forms.ValidationError("Enter at least one counted quantity.")
        return data


class StockFilterForm(forms.Form):
    LEVEL_CHOICES = [("", "All levels"), ("low", "Low stock"), ("out", "Out of stock")]
    q = forms.CharField(required=False, widget=forms.TextInput(attrs={"placeholder": "Product name", "type": "search"}))
    category = forms.ChoiceField(required=False)
    level = forms.ChoiceField(required=False, choices=LEVEL_CHOICES)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].choices = [("", "All categories")] + [
            (str(c.pk), c.name) for c in Category.objects.filter(is_active=True).order_by("name")
        ]
        self.fields["q"].widget.attrs["class"] = "form-control"
        for name in ("category", "level"):
            self.fields[name].widget.attrs.update({"class": "form-select", "data-auto-submit": ""})


class MovementFilterForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.order_by("name"), required=False,
                                     empty_label="All products")
    kind = forms.ChoiceField(required=False, choices=[("", "All movements")] + list(StockMovement.Kind.choices))
    date_from = forms.DateField(required=False, label="From", widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, label="To", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            is_select = isinstance(field, (forms.ChoiceField, forms.ModelChoiceField))
            field.widget.attrs["class"] = "form-select" if is_select else "form-control"
            if is_select:
                field.widget.attrs["data-auto-submit"] = ""
