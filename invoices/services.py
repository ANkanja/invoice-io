"""
Invoice lifecycle: create draft → add lines → issue → (cancel → replacement).

Views and forms (Phase 3) call these functions; they never change invoice
status, totals or stock themselves.
"""

import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.models import AuditLog, BusinessSettings
from core.money import quantize, to_decimal
from inventory.services import deduct_stock_for_invoice, restore_stock_for_invoice

from .calculations import calculate_invoice, calculate_line
from .models import Invoice, InvoiceLine
from .numbering import allocate_invoice_number

Status = Invoice.Status


class InvoiceStateError(ValidationError):
    pass


def _copy_customer(invoice: Invoice, customer) -> None:
    invoice.customer = customer
    invoice.customer_name = customer.name
    invoice.customer_contact_person = customer.contact_person
    invoice.customer_phone = customer.phone
    invoice.customer_email = customer.email
    invoice.customer_address = customer.billing_address
    invoice.customer_tax_pin = customer.tax_pin


def create_draft(*, user, customer=None, customer_name: str = "", issue_date=None, **fields) -> Invoice:
    """New draft pre-filled from Business Settings. Pass `customer` for a saved
    customer, or `customer_name` (+ other customer_* fields) for a one-time one."""
    settings = BusinessSettings.load()
    issue_date = issue_date or timezone.localdate()
    invoice = Invoice(
        created_by=user,
        issue_date=issue_date,
        currency=settings.currency,
        payment_terms=settings.default_payment_terms,
        notes=settings.default_invoice_notes,
        tax_label=settings.tax_label if settings.tax_enabled else "",
        tax_rate=settings.effective_tax_rate(),
        prices_include_tax=settings.prices_include_tax,
    )
    if settings.default_payment_terms_days is not None:
        invoice.due_date = issue_date + datetime.timedelta(days=settings.default_payment_terms_days)
    if customer is not None:
        _copy_customer(invoice, customer)
    else:
        invoice.customer_name = customer_name.strip()
    for key, value in fields.items():
        setattr(invoice, key, value)
    if not invoice.customer_name:
        raise ValidationError("Enter a customer name.")
    invoice.full_clean(exclude=["created_by"])
    invoice.save()
    return invoice


def _require_draft(invoice: Invoice) -> None:
    if invoice.status != Status.DRAFT:
        raise InvoiceStateError(f"Invoice {invoice.number} is {invoice.get_status_display().lower()} and cannot be edited.")


@transaction.atomic
def add_line(invoice: Invoice, *, product=None, quantity, unit_price=None, line_discount="0",
             description: str = "", unit: str = "", package_size: str = "") -> InvoiceLine:
    """Add a line. With a product, its name, unit, size and default price are
    copied in; `unit_price` overrides the price for this invoice only."""
    _require_draft(invoice)
    catalogue_price = None
    if product is not None:
        catalogue_price = product.default_price
        description = description or str(product.name)
        unit = unit or product.unit
        package_size = package_size or product.package_size
        if unit_price is None:
            unit_price = product.default_price
    if not description.strip():
        raise ValidationError("Each line needs a description.")
    if unit_price is None:
        raise ValidationError("Enter a unit price.")
    result = calculate_line(quantity, unit_price, line_discount)
    position = (invoice.lines.aggregate(m=Max("position"))["m"] or 0) + 1
    line = InvoiceLine.objects.create(
        invoice=invoice, position=position, product=product, description=description.strip(),
        unit=unit, package_size=package_size, catalogue_price=catalogue_price,
        quantity=to_decimal(quantity), unit_price=quantize(unit_price),
        line_discount=result.discount, line_total=result.total,
    )
    recalculate(invoice)
    return line


@transaction.atomic
def recalculate(invoice: Invoice) -> Invoice:
    """Recompute and store line totals and invoice totals for a draft."""
    _require_draft(invoice)
    lines = list(invoice.lines.all())
    totals = calculate_invoice(lines, invoice.invoice_discount, invoice.tax_rate, invoice.prices_include_tax)
    for line, result in zip(lines, totals.lines):
        if line.line_total != result.total:
            line.line_total = result.total
            line.save(update_fields=["line_total"])
    invoice.subtotal = totals.subtotal
    invoice.tax_total = totals.tax_total
    invoice.total = totals.total
    invoice.save(update_fields=["subtotal", "tax_total", "total", "updated_at"])
    return invoice


@transaction.atomic
def issue_invoice(invoice: Invoice, user) -> Invoice:
    """Finalise a draft: recalculate on the server, freeze seller details,
    allocate the number and deduct stock — all or nothing."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    _require_draft(invoice)

    settings = BusinessSettings.load()
    if not settings.is_configured:
        raise InvoiceStateError("Add your business name and phone number in Settings before issuing invoices.")
    if not invoice.lines.exists():
        raise InvoiceStateError("Add at least one line before issuing.")
    if not invoice.customer_name.strip():
        raise InvoiceStateError("The invoice needs a customer name.")
    if invoice.due_date and invoice.due_date < invoice.issue_date:
        raise InvoiceStateError("The due date cannot be before the issue date.")

    recalculate(invoice)  # never trust previously stored or client-sent totals

    invoice.number = allocate_invoice_number(
        prefix=settings.invoice_prefix,
        padding=settings.invoice_number_padding,
        reset_yearly=settings.reset_numbering_yearly,
        on_date=invoice.issue_date,
    )
    invoice.business_snapshot = settings.snapshot()
    invoice.status = Status.ISSUED
    invoice.issued_at = timezone.now()
    invoice.issued_by = user
    invoice.save()

    deduct_stock_for_invoice(invoice, user)  # raises → whole issue rolls back, number included
    AuditLog.record(actor=user, action="invoice.issued", obj=invoice, changes={"total": str(invoice.total)})
    return invoice


@transaction.atomic
def cancel_invoice(invoice: Invoice, user, reason: str) -> Invoice:
    """Cancel an issued invoice and return its stock. Payments must be reversed
    (or, from Phase 4, moved to the replacement) first."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != Status.ISSUED:
        raise InvoiceStateError("Only issued invoices can be cancelled. Drafts can simply be deleted.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Give a reason for cancelling.")
    if invoice.get_amount_paid() > 0:
        raise InvoiceStateError(
            "This invoice has payments recorded against it. Reverse those payments before cancelling."
        )
    invoice.status = Status.CANCELLED
    invoice.cancelled_at = timezone.now()
    invoice.cancelled_by = user
    invoice.cancel_reason = reason
    invoice.save()

    restore_stock_for_invoice(invoice, user)
    AuditLog.record(actor=user, action="invoice.cancelled", obj=invoice, reason=reason)
    return invoice


@transaction.atomic
def create_replacement_draft(cancelled: Invoice, user) -> Invoice:
    """Start a corrected copy of a cancelled invoice. It is an ordinary draft:
    edit it, then issue it (which deducts stock again)."""
    cancelled = Invoice.objects.select_for_update().get(pk=cancelled.pk)
    if cancelled.status != Status.CANCELLED:
        raise InvoiceStateError("Only a cancelled invoice can be replaced.")
    if Invoice.objects.filter(replaces=cancelled).exists():
        raise InvoiceStateError(f"Invoice {cancelled.number} already has a replacement.")
    draft = Invoice.objects.create(
        created_by=user,
        replaces=cancelled,
        customer=cancelled.customer,
        customer_name=cancelled.customer_name,
        customer_contact_person=cancelled.customer_contact_person,
        customer_phone=cancelled.customer_phone,
        customer_email=cancelled.customer_email,
        customer_address=cancelled.customer_address,
        customer_tax_pin=cancelled.customer_tax_pin,
        issue_date=timezone.localdate(),
        due_date=cancelled.due_date if cancelled.due_date and cancelled.due_date >= timezone.localdate() else None,
        currency=cancelled.currency,
        payment_terms=cancelled.payment_terms,
        notes=cancelled.notes,
        invoice_discount=cancelled.invoice_discount,
        tax_label=cancelled.tax_label,
        tax_rate=cancelled.tax_rate,
        prices_include_tax=cancelled.prices_include_tax,
    )
    for line in cancelled.lines.all():
        InvoiceLine.objects.create(
            invoice=draft, position=line.position, product=line.product, description=line.description,
            unit=line.unit, package_size=line.package_size, catalogue_price=line.catalogue_price,
            quantity=line.quantity, unit_price=line.unit_price, line_discount=line.line_discount,
            line_total=line.line_total,
        )
    recalculate(draft)
    AuditLog.record(actor=user, action="invoice.replacement_started", obj=draft,
                    changes={"replaces": cancelled.number})
    return draft
