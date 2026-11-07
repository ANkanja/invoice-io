from django import forms
from django.db.models import Q

from core.forms import BootstrapFormMixin

from .models import Customer

class CustomerForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["kind", "name", "contact_person", "phone", "email", "billing_address", "tax_pin", "notes"]
        labels = {
            "kind": "Type",
            "name": "Name",
            "billing_address": "Address for invoices",
        }
        help_texts = {
            "contact_person": "For organisations: who you deal with, e.g. the school bursar.",
            "tax_pin": "Optional. Printed on invoices if entered.",
            "notes": "Private notes, never printed.",
        }
        widgets = {
            "kind": forms.RadioSelect,
            "billing_address": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "phone": forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "off"}),
            "email": forms.EmailInput(attrs={"autocomplete": "off"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_bootstrap()
        self.fields["kind"].widget.attrs["class"] = "form-check-input"

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

    def clean_phone(self):
        return " ".join(self.cleaned_data["phone"].split())

    def clean_tax_pin(self):
        return self.cleaned_data["tax_pin"].strip().upper()

    def possible_duplicates(self):
        """Other customers with the same phone or name (case-insensitive), to warn about."""
        qs = Customer.objects.exclude(pk=self.instance.pk)
        name, phone = self.cleaned_data.get("name"), self.cleaned_data.get("phone")

        cond = Q(name__iexact=name) if name else Q(pk__in=[])
        if phone:
            cond |= Q(phone=phone)
        return list(qs.filter(cond)[:3])

class CustomerFilterForm(forms.Form):
    STATUS_CHOICES = [("active", "Active"), ("archived", "Archived"), ("all", "All")]
    KIND_CHOICES = [("", "All types")] + list(Customer.Kind.choices)

    q = forms.CharField(required=False, widget=forms.TextInput(attrs={"placeholder": "Name, phone or email", "type": "search"}))
    kind = forms.ChoiceField(required=False, choices=KIND_CHOICES)
    status = forms.ChoiceField(required=False, choices=STATUS_CHOICES)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["q"].widget.attrs["class"] = "form-control"
        for name in ("kind", "status"):
            self.fields[name].widget.attrs.update({"class": "form-select", "data-auto-submit": ""})
