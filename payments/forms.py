import uuid
from decimal import Decimal

from django import forms
from django.utils import timezone

from core.forms import BootstrapFormMixin
from customers.models import Customer

from .models import PaymentMethod

MONEY_ATTRS = {"inputmode": "decimal", "step": "0.01", "min": "0", "autocomplete": "off"}


def money_field(**kwargs) -> forms.DecimalField:
    attrs = {**MONEY_ATTRS, **kwargs.pop("attrs", {})}
    return forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0"),
                              widget=forms.NumberInput(attrs=attrs), **kwargs)


class AllocationFieldsMixin:
    """Adds one "apply KES … to this invoice" box per open invoice (field
    alloc_<invoice pk>). `self.invoices` must have `balance` annotated."""

    def add_allocation_fields(self, invoices):
        self.invoices = list(invoices)
        for inv in self.invoices:
            self.fields[f"alloc_{inv.pk}"] = money_field(
                required=False, label=inv.number,
                attrs={"class": "form-control form-control-sm alloc-input", "data-alloc": "",
                       "data-balance": str(inv.balance), "max": str(inv.balance)},
            )

    def allocation_rows(self):
        for inv in self.invoices:
            yield inv, self[f"alloc_{inv.pk}"]

    def clean_allocations(self) -> list[tuple]:
        rows = []
        for inv in self.invoices:
            amount = self.cleaned_data.get(f"alloc_{inv.pk}")
            if not amount:
                continue
            if amount > inv.balance:
                self.add_error(f"alloc_{inv.pk}", f"Only {inv.balance:,.2f} is still due on this invoice.")
            rows.append((inv, amount))
        return rows


class PaymentForm(AllocationFieldsMixin, BootstrapFormMixin, forms.Form):
    customer = forms.ModelChoiceField(queryset=Customer.objects.none(), required=False,
                                      empty_label="Not a saved customer", label="Customer")
    payer_name = forms.CharField(max_length=150, required=False, label="Paid by",
                                 help_text="Leave blank to use the customer's name.")
    amount = money_field(label="Amount (KES)", attrs={"data-payment-amount": ""})
    payment_date = forms.DateField(label="Date received", initial=timezone.localdate,
                                   widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    method = forms.ModelChoiceField(queryset=PaymentMethod.objects.none(), empty_label=None, label="Method")
    reference = forms.CharField(max_length=60, required=False, label="Reference",
                                help_text="M-Pesa code, bank reference or cheque number.")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, invoices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True).order_by("name")
        self.fields["customer"].widget.attrs["data-payment-customer"] = ""
        self.fields["method"].queryset = PaymentMethod.objects.filter(is_active=True)
        self.fields["method"].widget.attrs["data-payment-method"] = ""
        self.fields["payment_date"].widget.attrs["max"] = timezone.localdate().isoformat()
        if not self.is_bound:
            self.initial.setdefault("token", uuid.uuid4())
        self.add_allocation_fields(invoices)
        self._apply_bootstrap()

    def clean_reference(self):
        return (self.cleaned_data.get("reference") or "").strip().upper()

    def clean(self):
        data = super().clean()
        customer, method = data.get("customer"), data.get("method")
        if not customer and not (data.get("payer_name") or "").strip():
            self.add_error("payer_name", "Say who paid, or choose the customer.")
        if method and method.requires_reference and not data.get("reference"):
            self.add_error("reference", f"Enter the {method} reference.")
        date = data.get("payment_date")
        if date and date > timezone.localdate():
            self.add_error("payment_date", "The date can't be in the future.")
        rows = self.clean_allocations()
        amount = data.get("amount")
        if amount is not None and amount <= 0:
            self.add_error("amount", "Enter the amount received.")
        elif amount is not None and sum((a for _, a in rows), Decimal("0")) > amount:
            self.add_error(None, "You've applied more to invoices than the amount received.")
        data["allocations"] = rows
        return data


class ApplyCreditForm(AllocationFieldsMixin, forms.Form):
    def __init__(self, *args, invoices=(), credit=Decimal("0"), **kwargs):
        super().__init__(*args, **kwargs)
        self.credit = credit
        self.add_allocation_fields(invoices)

    def clean(self):
        data = super().clean()
        rows = self.clean_allocations()
        if not rows and not self.errors:
            raise forms.ValidationError("Enter an amount for at least one invoice.")
        if sum((a for _, a in rows), Decimal("0")) > self.credit:
            raise forms.ValidationError(f"That's more than the {self.credit:,.2f} credit left on this payment.")
        data["allocations"] = rows
        return data


class ReversePaymentForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={"rows": 3}),
                             label="Why are you reversing this payment?",
                             help_text="E.g. “Entered twice”, “Cheque bounced”, “Refunded to customer”.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_bootstrap()


class PaymentFilterForm(forms.Form):
    STATE_CHOICES = [("", "All payments"), ("credit", "With unused credit"), ("reversed", "Reversed")]
    q = forms.CharField(required=False, widget=forms.TextInput(attrs={"placeholder": "Payer, reference or invoice no.", "type": "search"}))
    state = forms.ChoiceField(required=False, choices=STATE_CHOICES)
    method = forms.ModelChoiceField(queryset=PaymentMethod.objects.all(), required=False, empty_label="All methods")
    customer = forms.ModelChoiceField(queryset=Customer.objects.order_by("name"), required=False, empty_label="All customers")
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            is_select = isinstance(field, (forms.ChoiceField, forms.ModelChoiceField))
            field.widget.attrs["class"] = "form-select" if is_select else "form-control"
            if is_select:
                field.widget.attrs["data-auto-submit"] = ""
