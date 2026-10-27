from django import forms
from django.conf import settings

from .models import BusinessSettings


class BootstrapFormMixin:
    """Adds Bootstrap classes to widgets so templates stay simple."""

    def _apply_bootstrap(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput,)):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")

    def full_clean(self):
        super().full_clean()
        # Mark invalid fields so Bootstrap shows them in red.
        for name in self.errors:
            if name in self.fields:
                attrs = self.fields[name].widget.attrs
                attrs["class"] = f"{attrs.get('class', '')} is-invalid".strip()
                attrs["aria-invalid"] = "true"


class BusinessSettingsForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = BusinessSettings
        fields = [
            "name", "logo", "phone", "email", "address", "kra_pin", "registration_number",
            "currency", "invoice_prefix", "invoice_number_padding", "reset_numbering_yearly",
            "default_payment_terms_days", "default_payment_terms", "default_invoice_notes", "payment_instructions",
            "tax_enabled", "tax_label", "tax_rate", "prices_include_tax",
            "allow_negative_stock",
        ]
        widgets = {
            "logo": forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
            "address": forms.Textarea(attrs={"rows": 3}),
            "default_payment_terms": forms.Textarea(attrs={"rows": 2}),
            "default_invoice_notes": forms.Textarea(attrs={"rows": 2}),
            "payment_instructions": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "invoice_number_padding": "Digits in invoice number",
            "default_payment_terms_days": "Payment due after (days)",
            "allow_negative_stock": "Allow issuing invoices when stock would go below zero",
        }

    WIDE_FIELDS = ["address", "default_payment_terms", "default_invoice_notes", "payment_instructions"]

    # Groups shown as sections on the settings page.
    SECTIONS = [
        ("Business details", "Shown on invoices and in the app.", [
            "name", "logo", "phone", "email", "address", "kra_pin", "registration_number"]),
        ("Invoices", "Numbering and the defaults every new invoice starts with.", [
            "currency", "invoice_prefix", "invoice_number_padding", "reset_numbering_yearly",
            "default_payment_terms_days", "default_payment_terms", "default_invoice_notes", "payment_instructions"]),
        ("Tax", "Leave tax off unless you are registered to charge it.", [
            "tax_enabled", "tax_label", "tax_rate", "prices_include_tax"]),
        ("Stock", "How issuing an invoice treats stock levels.", ["allow_negative_stock"]),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = True
        self.fields["phone"].required = True
        self.fields["logo"].widget.attrs["data-max-mb"] = str(settings.MAX_UPLOAD_MB)
        self.fields["currency"].widget.attrs.update({"maxlength": 3, "style": "text-transform:uppercase"})
        self.fields["invoice_prefix"].widget.attrs["style"] = "text-transform:uppercase"
        self._apply_bootstrap()

    def clean_currency(self):
        value = self.cleaned_data["currency"].strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise forms.ValidationError("Use a 3-letter currency code, e.g. KES.")
        return value

    def clean_invoice_prefix(self):
        return self.cleaned_data["invoice_prefix"].strip().upper()

    def clean(self):
        data = super().clean()
        if data.get("tax_enabled") and not data.get("tax_rate"):
            self.add_error("tax_rate", "Enter the tax rate, or switch tax off.")
        return data

    def sections(self):
        for title, help_text, names in self.SECTIONS:
            yield title, help_text, [self[name] for name in names]
