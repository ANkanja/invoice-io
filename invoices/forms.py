"""Invoice editor forms: a header form plus a formset of lines.

All amounts are re-validated and recalculated on the server by
invoices.services.save_draft; nothing the browser computes is trusted.
"""

import datetime
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.forms import BootstrapFormMixin
from customers.models import Customer
from core.money import quantize
from products.models import Product, SellingOption, Unit
from products.pricing import price_for, resolve_quantity

from .models import Invoice

MONEY = {"max_digits": 12, "decimal_places": 2}
NUM_ATTRS = {"inputmode": "decimal", "step": "0.01", "min": "0", "autocomplete": "off"}
MAX_LINES = 200


class InvoiceHeaderForm(BootstrapFormMixin, forms.Form):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.none(), required=False, empty_label="One-time customer (type details below)",
        label="Customer",
    )
    customer_name = forms.CharField(required=False, max_length=150, label="Name")
    customer_contact_person = forms.CharField(required=False, max_length=120, label="Contact person")
    customer_phone = forms.CharField(required=False, max_length=30, label="Phone")
    customer_email = forms.EmailField(required=False, label="Email")
    customer_address = forms.CharField(required=False, label="Address",
                                       widget=forms.Textarea(attrs={"rows": 2}))
    customer_tax_pin = forms.CharField(required=False, max_length=30, label="KRA PIN")
    customer_kind = forms.ChoiceField(
        choices=Customer.Kind.choices, initial=Customer.Kind.INDIVIDUAL, label="Customer type", required=False,
        widget=forms.RadioSelect(attrs={"data-customer-kind": ""}),
        help_text="Sets the prices and payment terms. Filled in from the saved customer; you can change it.",
    )

    issue_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                               help_text="Leave blank if there is no due date.")
    invoice_discount = forms.DecimalField(required=False, min_value=Decimal("0"), **MONEY, label="Discount on whole invoice (KES)",
                                          widget=forms.NumberInput(attrs={**NUM_ATTRS, "data-invoice-discount": ""}))
    payment_terms = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                            help_text="Printed on the invoice.")

    def __init__(self, *args, invoice: Invoice | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        customers = Customer.objects.filter(is_active=True)
        if invoice is not None and invoice.customer_id:
            customers = customers | Customer.objects.filter(pk=invoice.customer_id)
        self.fields["customer"].queryset = customers.distinct().order_by("name")
        self.fields["customer"].widget.attrs["data-customer-select"] = ""
        self._apply_bootstrap()
        self.fields["customer_kind"].widget.attrs["class"] = "form-check-input"

    def clean(self):
        data = super().clean()
        if not data.get("customer") and not (data.get("customer_name") or "").strip():
            self.add_error("customer_name", "Choose a saved customer or type the customer's name.")
        issue, due = data.get("issue_date"), data.get("due_date")
        if issue and due and due < issue:
            self.add_error("due_date", "The due date can't be before the issue date.")
        if issue and abs((issue - datetime.date.today()).days) > 366 * 2:
            self.add_error("issue_date", "Check the date: it is more than two years away from today.")
        data["invoice_discount"] = data.get("invoice_discount") or Decimal("0.00")
        return data


class InvoiceLineForm(forms.Form):
    """One line. With a product, the selling option decides how it is priced
    (per pack, or per L/kg) and `entered_unit` is the unit the quantity was
    typed in. Without a product it's a free-text line with its own unit."""

    product = forms.ModelChoiceField(queryset=Product.objects.none(), required=False)
    selling_option = forms.ModelChoiceField(queryset=SellingOption.objects.none(), required=False,
                                            label="Sold as")
    description = forms.CharField(required=False, max_length=255)
    quantity = forms.DecimalField(min_value=Decimal("0.01"), max_digits=10, decimal_places=2,
                                  widget=forms.NumberInput(attrs={**NUM_ATTRS, "data-line-qty": ""}))
    entered_unit = forms.CharField(required=False, max_length=20, label="Unit")
    free_unit = forms.CharField(required=False, max_length=30, label="Unit")
    unit_price = forms.DecimalField(required=False, min_value=Decimal("0"), **MONEY, label="Price",
                                    widget=forms.NumberInput(attrs={**NUM_ATTRS, "data-line-price": ""}))
    line_discount = forms.DecimalField(required=False, min_value=Decimal("0"), **MONEY, label="Discount",
                                       widget=forms.NumberInput(attrs={**NUM_ATTRS, "data-line-discount": ""}))

    def __init__(self, *args, products=None, options=None, units=None, customer_kind=Customer.Kind.INDIVIDUAL,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.customer_kind = customer_kind
        if products is not None:
            self.fields["product"].queryset = products
        if options is not None:
            self.fields["selling_option"].queryset = options
        self.units = units if units is not None else {u.code: u for u in Unit.objects.all()}
        self.fields["free_unit"].widget.attrs["placeholder"] = "e.g. trip"
        for name, field in self.fields.items():
            is_select = name in ("product", "selling_option")
            field.widget.attrs.setdefault("class", "form-select form-select-sm" if is_select else "form-control form-control-sm")

    def clean(self):
        data = super().clean()
        product, option = data.get("product"), data.get("selling_option")
        qty, price = data.get("quantity"), data.get("unit_price")
        data["line_discount"] = data.get("line_discount") or Decimal("0.00")
        code = (data.get("entered_unit") or "").strip()
        data["entered_unit"] = None

        if option is not None and product is None:
            product = data["product"] = option.product
        if product is not None:
            if option is not None and option.product_id != product.pk:
                self.add_error("selling_option", "Choose how this product is sold.")
                return data
            if option is None:
                option = data["selling_option"] = product.default_option
            if option is None:
                self.add_error("product", f"{product} has no way to sell it yet. Add a price on the product page.")
                return data
            unit = None
            if code:
                unit = self.units.get(code)
                if unit is None:
                    self.add_error("entered_unit", "Choose a unit from the list.")
                    return data
            data["entered_unit"] = unit
            if qty is None:
                return data
            try:
                priced = resolve_quantity(option, qty, unit)
            except ValidationError as exc:
                self.add_error("entered_unit" if unit is not None else "quantity", exc.messages[0])
                return data
            gross = quantize(priced.price_quantity * (price_for(option, self.customer_kind) if price is None else price))
        else:
            if not (data.get("description") or "").strip():
                self.add_error("description", "Describe the item, or choose a product.")
            if price is None:
                self.add_error("unit_price", "Enter a price.")
                return data
            if qty is None:
                return data
            gross = quantize(qty * price)
        if data["line_discount"] > gross:
            self.add_error("line_discount", "The discount is more than the line amount.")
        return data


class BaseLineFormSet(forms.BaseFormSet):
    """Every row is optional (fully blank rows are ignored), but at least one
    real line is required. Rows are renumbered in the browser when removed."""

    def __init__(self, *args, products=None, options=None, customer_kind=Customer.Kind.INDIVIDUAL, **kwargs):
        self.products = products
        self.options = options
        self.customer_kind = customer_kind
        self.units = {u.code: u for u in Unit.objects.all()}
        super().__init__(*args, **kwargs)

    def initial_form_count(self):
        return 0  # treat all rows as "extra", so empty ones are skipped

    def get_form_kwargs(self, index):
        # Also used for the blank row template (empty_form), so new rows get the same product list.
        return {**super().get_form_kwargs(index), "products": self.products, "options": self.options,
                "units": self.units, "customer_kind": self.customer_kind}

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not self.cleaned_lines():
            raise forms.ValidationError("Add at least one line.")

    def cleaned_lines(self) -> list[dict]:
        return [f.cleaned_data for f in self.forms if f.has_changed() and f.cleaned_data]


def make_line_formset(*, data=None, initial=None, products=None, options=None,
                      customer_kind=Customer.Kind.INDIVIDUAL):
    initial = initial or []
    FormSet = forms.formset_factory(
        InvoiceLineForm, formset=BaseLineFormSet, extra=max(len(initial), 1),
        max_num=MAX_LINES, validate_max=True, absolute_max=MAX_LINES,
    )
    return FormSet(data=data, initial=initial, prefix="lines", products=products, options=options,
                   customer_kind=customer_kind)


def products_for(invoice: Invoice | None):
    """Active products, plus any already on this draft (even if archived since)."""
    cond = Q(is_active=True)
    if invoice is not None and invoice.pk:
        cond |= Q(pk__in=invoice.lines.exclude(product=None).values("product_id"))
    return (Product.objects.filter(cond).select_related("category", "stock_unit").order_by("name")
            .prefetch_related(models.Prefetch(
                "options", queryset=SellingOption.objects.select_related("package_unit", "price_unit"))))


def options_for(invoice: Invoice | None):
    """Active selling options of active products, plus any already on this invoice."""
    cond = Q(is_active=True, product__is_active=True)
    if invoice is not None and invoice.pk:
        cond |= Q(pk__in=invoice.lines.exclude(selling_option=None).values("selling_option_id"))
    return SellingOption.objects.filter(cond).select_related("product__stock_unit", "package_unit", "price_unit")


class CancelInvoiceForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), max_length=500,
                             label="Why are you cancelling this invoice?",
                             help_text="Kept with the invoice for your records, e.g. “Wrong quantities; replaced”.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_bootstrap()


class AmendInvoiceForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), max_length=500,
                             label="What did you change?",
                             help_text="Kept with the invoice's history, e.g. “Added 2 × 5 L soap left off the first copy”.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_bootstrap()


class InvoiceFilterForm(forms.Form):
    STATE_CHOICES = [
        ("", "All invoices"), ("outstanding", "Not fully paid"), ("draft", "Drafts"), ("unpaid", "Unpaid"),
        ("partial", "Partially paid"), ("overdue", "Overdue"), ("paid", "Paid"), ("cancelled", "Cancelled"),
    ]
    q = forms.CharField(required=False, widget=forms.TextInput(attrs={"placeholder": "Invoice number or customer", "type": "search"}))
    state = forms.ChoiceField(required=False, choices=STATE_CHOICES)
    customer = forms.ModelChoiceField(queryset=Customer.objects.order_by("name"), required=False, empty_label="All customers")
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            is_select = name in ("state", "customer")
            field.widget.attrs["class"] = "form-select" if is_select else "form-control"
            if is_select:
                field.widget.attrs["data-auto-submit"] = ""
