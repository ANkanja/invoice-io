"""
Invoice lifecycle: create draft → add lines → issue → (amend | cancel → replacement).

Views and forms call these functions; they never change invoice status,
totals or stock themselves. Every line goes through products.pricing, so
units, conversions and pack prices are handled in exactly one place.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from core.models import AuditLog, BusinessSettings
from customers.models import Customer
from core.money import quantize, to_decimal
from inventory.services import deduct_stock_for_invoice, restore_stock_for_invoice
from payments.services import apply_released_to_replacement, release_invoice_payments
from products.models import SellingOption, Unit
from products.pricing import price_for, resolve_quantity

from .calculations import calculate_invoice, calculate_line
from .models import Invoice, InvoiceLine, InvoiceRevision
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


def _require_draft(invoice: Invoice) -> None:
    if invoice.status != Status.DRAFT:
        raise InvoiceStateError(f"Invoice {invoice.number} is {invoice.get_status_display().lower()} and cannot be edited.")


def _as_unit(value) -> Unit | None:
    if value in (None, ""):
        return None
    if isinstance(value, Unit):
        return value
    unit = Unit.objects.filter(Q(code=value) | Q(symbol=value) | Q(name=value)).first()
    if unit is None:
        raise ValidationError(f"Unknown unit “{value}”.")
    return unit


# --- Building lines ---------------------------------------------------------------

def build_line(invoice: Invoice, position: int, data: dict) -> InvoiceLine:
    """An unsaved InvoiceLine with its full pricing snapshot.

    data keys: product, selling_option, quantity, entered_unit (Unit/code; product
    lines), free_unit (text; other lines), unit_price (None → the option's price
    for the invoice's customer type), line_discount, description.
    """
    product = data.get("product")
    option: SellingOption | None = data.get("selling_option")
    if product is not None and option is None:
        option = product.default_option
        if option is None:
            raise ValidationError(f"{product} has no active way to sell it yet. Add one on the product page.")
    if option is not None:
        if product is not None and option.product_id != product.pk:
            raise ValidationError("That selling option belongs to a different product.")
        product = option.product

    discount = data.get("line_discount") or Decimal("0")
    description = (data.get("description") or "").strip()
    if option is not None:
        priced = resolve_quantity(option, data["quantity"], _as_unit(data.get("entered_unit")))
        catalogue_price = price_for(option, invoice.customer_kind)
        unit_price = data.get("unit_price")
        unit_price = catalogue_price if unit_price is None else unit_price
        result = calculate_line(priced.price_quantity, unit_price, discount)
        return InvoiceLine(
            invoice=invoice, position=position, product=product, selling_option=option,
            description=description or product.name, package_size=option.label,
            catalogue_price=catalogue_price, quantity=priced.entered_quantity,
            unit=priced.unit, unit_plural=priced.unit_plural, pricing_method=priced.pricing_method,
            price_unit=priced.price_unit, conversion_factor=priced.conversion_factor,
            price_quantity=priced.price_quantity, base_quantity=priced.base_quantity, base_unit=priced.base_unit,
            unit_price=quantize(unit_price), line_discount=result.discount, line_total=result.total,
        )

    # Free-text line ("Delivery", "Custom label printing"): no stock, no conversion.
    if not description:
        raise ValidationError("Each line needs a description.")
    unit_price = data.get("unit_price")
    if unit_price is None:
        raise ValidationError("Enter a unit price.")
    qty = to_decimal(data["quantity"])
    result = calculate_line(qty, unit_price, discount)
    free_unit = (data.get("free_unit") or "").strip()
    return InvoiceLine(
        invoice=invoice, position=position, description=description, quantity=qty,
        unit=free_unit, unit_plural=free_unit, price_unit=free_unit, conversion_factor=Decimal("1"),
        price_quantity=qty, unit_price=quantize(unit_price), line_discount=result.discount, line_total=result.total,
    )


def _line_snapshot(line: InvoiceLine) -> dict:
    fields = ["position", "description", "unit", "unit_plural", "package_size", "pricing_method", "price_unit",
              "conversion_factor", "price_quantity", "base_quantity", "base_unit", "catalogue_price", "quantity",
              "unit_price", "line_discount", "line_total"]
    data = {f: (str(v) if isinstance(v, Decimal) else v) for f in fields if (v := getattr(line, f)) is not None}
    data["product_id"] = line.product_id
    data["selling_option_id"] = line.selling_option_id
    return data


def _copy_line(line: InvoiceLine, invoice: Invoice, **overrides) -> InvoiceLine:
    """A new unsaved line with the same snapshot (prices and units as they were)."""
    keep = ["position", "product_id", "selling_option_id", "description", "unit", "unit_plural", "package_size",
            "pricing_method", "price_unit", "conversion_factor", "price_quantity", "base_quantity", "base_unit",
            "catalogue_price", "quantity", "unit_price", "line_discount", "line_total"]
    values = {f: getattr(line, f) for f in keep}
    values.update(overrides)
    return InvoiceLine(invoice=invoice, **values)


def _save_lines(lines: list[InvoiceLine], amending: bool = False) -> None:
    for line in lines:
        line._amending = amending
        line.save()


# --- Drafts --------------------------------------------------------------------------

def create_draft(*, user, customer=None, customer_name: str = "", issue_date=None, customer_kind=None,
                 **fields) -> Invoice:
    """New draft pre-filled from Business Settings. Pass `customer` for a saved
    customer, or `customer_name` (+ other customer_* fields) for a one-time one.
    The customer type (the saved customer's, unless `customer_kind` is given)
    picks the payment terms and the prices of lines added later."""
    settings = BusinessSettings.load()
    issue_date = issue_date or timezone.localdate()
    kind = customer_kind or (customer.kind if customer is not None else Customer.Kind.INDIVIDUAL)
    days, terms = settings.payment_defaults(kind)
    invoice = Invoice(
        created_by=user, issue_date=issue_date, customer_kind=kind,
        payment_terms=terms, notes=settings.default_invoice_notes,
    )
    _apply_settings(invoice, settings)
    if days is not None:
        invoice.due_date = issue_date + datetime.timedelta(days=days)
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


@transaction.atomic
def add_line(invoice: Invoice, *, product=None, option=None, quantity, unit=None, unit_price=None,
             line_discount="0", description: str = "", free_unit: str = "") -> InvoiceLine:
    """Add one line to a draft. With a product (or one of its selling options)
    the pack/measure, unit and price come from the option; `unit_price`
    overrides the price for this invoice only."""
    _require_draft(invoice)
    position = (invoice.lines.aggregate(m=Max("position"))["m"] or 0) + 1
    line = build_line(invoice, position, {
        "product": product, "selling_option": option, "quantity": quantity, "entered_unit": unit,
        "unit_price": None if unit_price is None else to_decimal(unit_price),
        "line_discount": to_decimal(line_discount), "description": description, "free_unit": free_unit,
    })
    line.save()
    recalculate(invoice)
    return line


def _apply_totals(invoice: Invoice, lines: list[InvoiceLine]) -> None:
    totals = calculate_invoice(lines, invoice.invoice_discount, invoice.tax_rate, invoice.prices_include_tax)
    for line, result in zip(lines, totals.lines):
        line.line_total = result.total
    invoice.subtotal = totals.subtotal
    invoice.tax_total = totals.tax_total
    invoice.total = totals.total


@transaction.atomic
def recalculate(invoice: Invoice) -> Invoice:
    """Recompute and store line totals and invoice totals for a draft."""
    _require_draft(invoice)
    lines = list(invoice.lines.all())
    before = {line.pk: line.line_total for line in lines}
    _apply_totals(invoice, lines)
    for line in lines:
        if line.line_total != before[line.pk]:
            line.save(update_fields=["line_total"])
    invoice.save(update_fields=["subtotal", "tax_total", "total", "updated_at"])
    return invoice


def _apply_settings(invoice: Invoice, settings: BusinessSettings) -> None:
    """Drafts always follow the current Settings for currency and tax; issued
    invoices keep whatever applied when they were issued."""
    invoice.currency = settings.currency
    invoice.tax_label = settings.tax_label if settings.tax_enabled else ""
    invoice.tax_rate = settings.effective_tax_rate()
    invoice.prices_include_tax = settings.prices_include_tax


def new_draft_initial(today=None, customer_kind: str = Customer.Kind.INDIVIDUAL) -> dict:
    """Starting values for the 'new invoice' form, from Settings, for this type of customer."""
    settings = BusinessSettings.load()
    today = today or timezone.localdate()
    days, terms = settings.payment_defaults(customer_kind)
    due = today + datetime.timedelta(days=days) if days is not None else None
    return {"issue_date": today, "due_date": due, "payment_terms": terms, "customer_kind": customer_kind,
            "notes": settings.default_invoice_notes}


def _apply_header(invoice: Invoice, header: dict) -> None:
    customer = header.get("customer")
    if customer is not None:
        _copy_customer(invoice, customer)
    else:
        invoice.customer = None
        invoice.customer_name = " ".join((header.get("customer_name") or "").split())
        invoice.customer_contact_person = header.get("customer_contact_person", "").strip()
        invoice.customer_phone = header.get("customer_phone", "").strip()
        invoice.customer_email = header.get("customer_email", "").strip()
        invoice.customer_address = header.get("customer_address", "").strip()
        invoice.customer_tax_pin = header.get("customer_tax_pin", "").strip().upper()
    # The type can be changed on the invoice; otherwise it follows the saved customer.
    invoice.customer_kind = header.get("customer_kind") or (
        customer.kind if customer is not None else Customer.Kind.INDIVIDUAL)
    if not invoice.customer_name:
        raise ValidationError("The invoice needs a customer name.")
    invoice.issue_date = header["issue_date"]
    invoice.due_date = header.get("due_date")
    if invoice.due_date and invoice.due_date < invoice.issue_date:
        raise ValidationError("The due date can't be before the issue date.")
    invoice.payment_terms = header.get("payment_terms", "").strip()
    invoice.notes = header.get("notes", "").strip()
    invoice.invoice_discount = quantize(header.get("invoice_discount") or 0)


@transaction.atomic
def save_draft(invoice: Invoice | None, *, user, header: dict, lines: list[dict]) -> Invoice:
    """Create or update a draft from the editor's cleaned form data.

    Lines are replaced wholesale: drafts have no stock movements, so this is
    safe and keeps line order exactly as shown in the editor. Totals are
    recalculated here; any value the browser showed is ignored.
    """
    if invoice is None:
        invoice = Invoice(created_by=user)
    else:
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        _require_draft(invoice)
    if not lines:
        raise ValidationError("Add at least one line.")

    _apply_settings(invoice, BusinessSettings.load())
    _apply_header(invoice, header)
    built = [build_line(invoice, i, data) for i, data in enumerate(lines, start=1)]
    _apply_totals(invoice, built)  # raises (and rolls back) if e.g. the invoice discount is too big
    is_new = invoice.pk is None
    invoice.save()

    # Drafts only (checked above), so the per-line edit guard can be bypassed.
    InvoiceLine.all_objects.filter(invoice=invoice).delete()
    for line in built:
        line.invoice = invoice
    _save_lines(built)
    AuditLog.record(actor=user, action="invoice.draft_created" if is_new else "invoice.draft_saved", obj=invoice)
    return invoice


@transaction.atomic
def delete_draft(invoice: Invoice, user) -> None:
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    _require_draft(invoice)
    AuditLog.record(actor=user, action="invoice.draft_deleted", obj=invoice,
                    changes={"customer": invoice.customer_name, "total": str(invoice.total)})
    invoice.delete()


# --- Issue, cancel, replace, duplicate -----------------------------------------------------

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
        raise InvoiceStateError("The due date can't be before the issue date.")

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
    invoice.moved_payments = apply_released_to_replacement(invoice, user) if invoice.replaces_id else Decimal("0")
    return invoice


@transaction.atomic
def cancel_invoice(invoice: Invoice, user, reason: str) -> Invoice:
    """Cancel an issued invoice: return its stock, and turn any payments on it
    into credit (applied to the replacement when that is issued)."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != Status.ISSUED:
        raise InvoiceStateError("Only issued invoices can be cancelled. Drafts can simply be deleted.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Give a reason for cancelling.")
    invoice.status = Status.CANCELLED
    invoice.cancelled_at = timezone.now()
    invoice.cancelled_by = user
    invoice.cancel_reason = reason
    invoice.save()

    restore_stock_for_invoice(invoice, user)
    # Money already paid isn't lost: it becomes credit on those payments, and
    # issuing the replacement applies it there.
    released = release_invoice_payments(invoice, user, f"Invoice {invoice.number} cancelled: {reason}")
    invoice.released_payments = released
    AuditLog.record(actor=user, action="invoice.cancelled", obj=invoice, reason=reason,
                    changes={"payments_released": str(released)} if released else None)
    return invoice


def _new_draft_like(source: Invoice, user, *, replaces=None) -> Invoice:
    today = timezone.localdate()
    draft = Invoice(
        created_by=user, replaces=replaces, issue_date=today,
        due_date=source.due_date if source.due_date and source.due_date >= today else None,
        payment_terms=source.payment_terms, notes=source.notes, invoice_discount=source.invoice_discount,
    )
    _apply_settings(draft, BusinessSettings.load())
    for f in ("customer", "customer_name", "customer_contact_person", "customer_phone", "customer_email",
              "customer_address", "customer_tax_pin", "customer_kind"):
        setattr(draft, f, getattr(source, f))
    return draft


@transaction.atomic
def create_replacement_draft(cancelled: Invoice, user) -> Invoice:
    """Start a corrected copy of a cancelled invoice: same lines, units and
    prices as issued. It is an ordinary draft: edit it, then issue it."""
    cancelled = Invoice.objects.select_for_update().get(pk=cancelled.pk)
    if cancelled.status != Status.CANCELLED:
        raise InvoiceStateError("Only a cancelled invoice can be replaced.")
    if Invoice.objects.filter(replaces=cancelled).exists():
        raise InvoiceStateError(f"Invoice {cancelled.number} already has a replacement.")
    draft = _new_draft_like(cancelled, user, replaces=cancelled)
    draft.save()
    _save_lines([_copy_line(line, draft) for line in cancelled.lines.all()])
    recalculate(draft)
    AuditLog.record(actor=user, action="invoice.replacement_started", obj=draft,
                    changes={"replaces": cancelled.number})
    return draft


@transaction.atomic
def duplicate_invoice(source: Invoice, user) -> Invoice:
    """New draft with the same customer and lines (for repeat orders). Lines
    whose selling option is still active are re-priced at today's price; others
    keep the price from the source invoice."""
    draft = _new_draft_like(source, user)
    if source.customer and source.customer.is_active:
        _copy_customer(draft, source.customer)  # latest details from the customer record
        draft.customer_kind = source.customer.kind
    elif source.customer:
        draft.customer = None
    draft.customer_kind = draft.customer_kind or Customer.Kind.INDIVIDUAL
    initial = new_draft_initial(customer_kind=draft.customer_kind)
    draft.issue_date, draft.due_date = initial["issue_date"], initial["due_date"]
    draft.invoice_discount = Decimal("0.00")
    draft.save()
    new_lines = []
    for line in source.lines.select_related("selling_option__product", "selling_option__package_unit",
                                            "selling_option__price_unit"):
        option = line.selling_option
        if option is not None and option.is_active and option.product.is_active:
            new = build_line(draft, line.position, {
                "selling_option": option, "quantity": line.quantity, "entered_unit": line.unit,
                "description": line.description,
            })
            # Keep a line discount only if it still fits the (possibly new) price.
            gross = new.line_total + new.line_discount
            if line.line_discount and line.line_discount <= gross:
                new.line_discount = line.line_discount
        else:
            # Archived product/option or a free-text line: copy as a plain line at the old price.
            new = _copy_line(line, draft, product_id=None, selling_option_id=None, base_quantity=None, base_unit="")
        new_lines.append(new)
    _save_lines(new_lines)
    recalculate(draft)
    AuditLog.record(actor=user, action="invoice.duplicated", obj=draft,
                    changes={"from": source.number or f"draft {source.pk}"})
    return draft


# --- Amending issued invoices ----------------------------------------------------------------

def invoice_snapshot(invoice: Invoice) -> dict:
    header = ["number", "status", "customer_id", "customer_name", "customer_contact_person", "customer_phone",
              "customer_email", "customer_address", "customer_tax_pin", "customer_kind", "issue_date", "due_date", "currency",
              "payment_terms", "notes", "invoice_discount", "tax_label", "tax_rate", "prices_include_tax",
              "subtotal", "tax_total", "total", "business_snapshot", "revision"]
    data = {}
    for f in header:
        v = getattr(invoice, f)
        data[f] = str(v) if isinstance(v, (Decimal, datetime.date)) else v
    data["lines"] = [_line_snapshot(line) for line in invoice.lines.all()]
    return data


@transaction.atomic
def amend_invoice(invoice: Invoice, *, user, header: dict, lines: list[dict], reason: str) -> Invoice:
    """Correct an issued invoice in place: same number, new revision.

    1. The current version is archived as an InvoiceRevision (with the reason).
    2. Its stock is returned (reversal movements) and its lines are marked as
       superseded (kept, because the stock history points at them).
    3. The new lines are priced by the server, totals recalculated, and stock
       deducted for them. All or nothing: any problem (e.g. not enough stock,
       total below what's already paid) rolls everything back.
    Currency, tax and the seller details stay as originally issued.
    """
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != Status.ISSUED:
        raise InvoiceStateError("Only issued invoices can be amended. Cancelled invoices stay as they are.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Say what you changed, e.g. “Added omitted 5 L soap”.")
    if not lines:
        raise ValidationError("Add at least one line.")

    before_total = invoice.total
    InvoiceRevision.objects.create(invoice=invoice, revision_no=invoice.revision, snapshot=invoice_snapshot(invoice),
                                   reason=reason, created_by=user)
    restore_stock_for_invoice(invoice, user, note=f"Amended (revision {invoice.revision + 1})")
    InvoiceLine.all_objects.filter(invoice=invoice, is_current=True).update(is_current=False)

    _apply_header(invoice, header)
    built = [build_line(invoice, i, data) for i, data in enumerate(lines, start=1)]
    _apply_totals(invoice, built)
    paid = invoice.get_amount_paid()
    if paid > invoice.total:
        raise InvoiceStateError(
            f"The new total ({invoice.total}) would be less than what's already been paid ({paid})."
        )
    _save_lines(built, amending=True)
    invoice.revision += 1
    invoice.last_amended_at = timezone.now()
    invoice.last_amended_by = user
    invoice._amending = True
    invoice.save()
    invoice._amending = False

    deduct_stock_for_invoice(invoice, user)
    AuditLog.record(actor=user, action="invoice.amended", obj=invoice, reason=reason,
                    changes={"revision": invoice.revision, "total": [str(before_total), str(invoice.total)]})
    return invoice
