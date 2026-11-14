import datetime
import uuid
from decimal import Decimal

from django import forms
from django.utils import timezone

from core.forms import BootstrapFormMixin
from products.models import Product

from .services import month_start, next_month

MONEY_ATTRS = {"inputmode": "decimal", "step": "0.01", "min": "0", "autocomplete": "off"}
QTY_ATTRS = {"inputmode": "decimal", "step": "any", "min": "0", "autocomplete": "off"}


class NewPlanForm(BootstrapFormMixin, forms.Form):
    month = forms.ChoiceField(label="Month")
    budget = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=12, decimal_places=2,
                                label="Budget (KES)", help_text="Optional. How much you can spend on restocking.",
                                widget=forms.NumberInput(attrs=MONEY_ATTRS))

    def __init__(self, *args, taken: set[datetime.date], **kwargs):
        super().__init__(*args, **kwargs)
        this = month_start(timezone.localdate())
        options, month = [], this
        for _ in range(3):
            if month not in taken:
                options.append((month.isoformat(), f"{month:%B %Y}"))
            month = next_month(month)
        self.fields["month"].choices = options
        nxt = next_month(this).isoformat()
        self.fields["month"].initial = nxt if any(v == nxt for v, _ in options) else (options[0][0] if options else "")
        self._apply_bootstrap()

    def clean_month(self):
        return datetime.date.fromisoformat(self.cleaned_data["month"])


class PlanForm(BootstrapFormMixin, forms.Form):
    """Budget, notes and, per line, the planned quantity and cost (and remove)."""

    budget = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=12, decimal_places=2,
                                label="Budget (KES)", widget=forms.NumberInput(attrs={**MONEY_ATTRS, "data-plan-budget": ""}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Notes")

    def __init__(self, *args, plan, lines, **kwargs):
        super().__init__(*args, **kwargs)
        self.lines = lines
        for line in lines:
            self.fields[f"qty_{line.pk}"] = forms.DecimalField(
                min_value=Decimal("0"), max_digits=14, decimal_places=3, label=f"Quantity of {line.description}",
                initial=line.planned_qty.normalize(), widget=forms.NumberInput(attrs={**QTY_ATTRS, "data-line-qty": ""}))
            self.fields[f"cost_{line.pk}"] = forms.DecimalField(
                required=False, min_value=Decimal("0"), max_digits=12, decimal_places=2,
                label=f"Cost per unit of {line.description}", initial=line.planned_unit_cost,
                widget=forms.NumberInput(attrs={**MONEY_ATTRS, "data-line-cost": ""}))
            self.fields[f"remove_{line.pk}"] = forms.BooleanField(required=False, label=f"Remove {line.description}")
        self.initial.setdefault("budget", plan.budget)
        self.initial.setdefault("notes", plan.notes)
        self._apply_bootstrap()
        for line in lines:
            self.fields[f"remove_{line.pk}"].widget.attrs["class"] = "form-check-input"

    def line_values(self) -> list[dict]:
        return [{"line": line, "quantity": self.cleaned_data[f"qty_{line.pk}"],
                 "unit_cost": self.cleaned_data.get(f"cost_{line.pk}"),
                 "remove": self.cleaned_data.get(f"remove_{line.pk}")} for line in self.lines]


class AddProductForm(BootstrapFormMixin, forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none(), label="Product", empty_label="Choose a product")

    def __init__(self, *args, plan, **kwargs):
        super().__init__(*args, **kwargs)
        taken = plan.lines.exclude(product=None).values("product_id")
        self.fields["product"].queryset = Product.objects.filter(is_active=True).exclude(pk__in=taken).order_by("name")
        self._apply_bootstrap()


class AddItemForm(BootstrapFormMixin, forms.Form):
    description = forms.CharField(max_length=150, label="Item", help_text="e.g. Caustic soda, empty 5 L containers, transport.")
    unit = forms.CharField(max_length=30, required=False, label="Counted in", help_text="e.g. kg, pieces, trips.")
    quantity = forms.DecimalField(min_value=Decimal("0"), max_digits=14, decimal_places=3, label="Quantity",
                                  widget=forms.NumberInput(attrs=QTY_ATTRS))
    unit_cost = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=12, decimal_places=2,
                                   label="Cost per unit (KES)", widget=forms.NumberInput(attrs=MONEY_ATTRS))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_bootstrap()


class PurchaseForm(BootstrapFormMixin, forms.Form):
    quantity = forms.DecimalField(min_value=Decimal("0.001"), max_digits=14, decimal_places=3, label="Quantity bought",
                                  widget=forms.NumberInput(attrs=QTY_ATTRS))
    amount_paid = forms.DecimalField(min_value=Decimal("0"), max_digits=12, decimal_places=2, label="Total paid (KES)",
                                     widget=forms.NumberInput(attrs=MONEY_ATTRS))
    purchase_date = forms.DateField(label="Date bought", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    reference = forms.CharField(max_length=80, required=False, label="Supplier or receipt (optional)")
    token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, unit_label: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        if unit_label:
            self.fields["quantity"].label = f"Quantity bought ({unit_label})"
        if not self.is_bound:
            self.initial.setdefault("token", uuid.uuid4())
            self.initial.setdefault("purchase_date", timezone.localdate())
        self._apply_bootstrap()

    def clean_purchase_date(self):
        day = self.cleaned_data["purchase_date"]
        if day > timezone.localdate():
            raise forms.ValidationError("The date can't be in the future.")
        return day


class ReverseForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=200, label="Why are you reversing this purchase?")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_bootstrap()
