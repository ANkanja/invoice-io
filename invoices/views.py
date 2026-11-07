from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.models import BusinessSettings
from core.money import format_money
from core.pagination import paginate
from core.permissions import perm_required
from customers.models import Customer
from inventory.services import InsufficientStockError
from products.models import Unit
from products.pricing import units_for_option
from products.units import format_in_display_unit

from . import services
from .forms import (AmendInvoiceForm, CancelInvoiceForm, InvoiceFilterForm, InvoiceHeaderForm, make_line_formset,
                    options_for, products_for)
from .models import Invoice, InvoiceLine, InvoiceRevision
from .pdf import invoice_filename, render_invoice_image
from .sharing import share_data

Status = Invoice.Status


# --- List -----------------------------------------------------------------------

def filter_invoices(params):
    form = InvoiceFilterForm(params or None)
    data = form.cleaned_data if form.is_valid() else {}
    qs = Invoice.objects.with_payment_totals().select_related("customer")
    today = timezone.localdate()

    if q := (data.get("q") or "").strip():
        qs = qs.filter(Q(number__icontains=q) | Q(customer_name__icontains=q))
    if data.get("customer"):
        qs = qs.filter(customer=data["customer"])
    if data.get("date_from"):
        qs = qs.filter(issue_date__gte=data["date_from"])
    if data.get("date_to"):
        qs = qs.filter(issue_date__lte=data["date_to"])

    # Same rules as Invoice.payment_state(), expressed as database filters.
    issued = Q(status=Status.ISSUED)
    not_overdue = Q(due_date__isnull=True) | Q(due_date__gte=today)
    state = data.get("state") or ""
    state_filters = {
        "draft": Q(status=Status.DRAFT),
        "cancelled": Q(status=Status.CANCELLED),
        "outstanding": issued & Q(balance__gt=0),
        "paid": issued & Q(balance__lte=0),
        "overdue": issued & Q(balance__gt=0, due_date__lt=today),
        "partial": issued & Q(balance__gt=0, amount_paid__gt=0) & not_overdue,
        "unpaid": issued & Q(balance__gt=0, amount_paid=0) & not_overdue,
    }
    if state in state_filters:
        qs = qs.filter(state_filters[state])
    filtered = any(data.get(k) for k in ("q", "customer", "date_from", "date_to", "state"))
    return form, qs.order_by("-issue_date", "-id"), filtered


@require_GET
@perm_required("invoices.view_invoice")
def invoice_list(request):
    form, qs, filtered = filter_invoices(request.GET)
    issued = qs.filter(status=Status.ISSUED)
    total = issued.aggregate(s=Sum("total"))["s"] or 0
    paid = issued.aggregate(s=Sum("amount_paid"))["s"] or 0
    # Each figure gets its own query: combining aggregates over these annotations
    # in one query gave wrong results on SQLite.
    totals = {"total": total, "paid": paid, "balance": total - paid}
    return render(request, "invoices/invoice_list.html", {
        "filter_form": form, "page": paginate(request, qs), "filtered": filtered,
        "totals": totals, "has_any": Invoice.objects.exists(),
    })


# --- Editor -----------------------------------------------------------------------

def _units_data():
    return {
        u.code: {"symbol": u.symbol, "name": u.name, "plural": u.plural, "dimension": u.dimension,
                 "factor": str(u.factor)}
        for u in Unit.objects.all()
    }


def _reserved_by(invoice: Invoice | None) -> dict:
    """Stock this issued invoice already took, per product (it is returned
    first when an amendment is saved, so it counts as available)."""
    if invoice is None or invoice.status != Status.ISSUED:
        return {}
    reserved: dict = {}
    for line in invoice.lines.exclude(product=None).exclude(base_quantity=None):
        reserved[line.product_id] = reserved.get(line.product_id, 0) + line.base_quantity
    return reserved


def _product_options(products, options, invoice=None):
    """Data for the editor (TypeScript fills selling options, units and prices from it)."""
    reserved = _reserved_by(invoice)
    by_product: dict = {}
    for o in options:
        by_product.setdefault(o.product_id, []).append({
            "id": o.pk, "label": o.label, "method": o.method, "price": str(o.price),
            "institutionPrice": "" if o.institution_price is None else str(o.institution_price),
            "per": o.price_per_label, "isDefault": o.is_default, "active": o.is_active,
            "content": str(o.content_quantity or ""),
            "priceUnit": o.price_unit.code if o.price_unit else "",
            "packUnit": o.package_unit.code if o.package_unit else "",
            "units": [u.code for u in units_for_option(o)],
        })
    data = []
    for p in products:
        available = p.stock_qty + reserved.get(p.pk, 0)
        data.append({
            "id": p.pk, "name": p.name, "tracked": p.track_stock, "active": p.is_active,
            "category": p.category.name if p.category else "",
            "stockUnit": p.stock_unit.code, "stock": str(available),
            "stockDisplay": format_in_display_unit(available, p.stock_unit),
            "options": by_product.get(p.pk, []),
        })
    return data


def _line_initial(line: InvoiceLine, unit_codes: dict) -> dict:
    initial = {"product": line.product_id, "selling_option": line.selling_option_id, "description": line.description,
               "quantity": f"{line.quantity.normalize():f}", "unit_price": line.unit_price, "line_discount": line.line_discount or None}
    if line.product_id:
        initial["entered_unit"] = unit_codes.get(line.unit, "") if line.pricing_method == "measure" else ""
    else:
        initial["free_unit"] = line.unit
    return initial


def _header_initial(invoice: Invoice) -> dict:
    return {
        "customer": invoice.customer_id, "customer_name": invoice.customer_name,
        "customer_contact_person": invoice.customer_contact_person, "customer_phone": invoice.customer_phone,
        "customer_email": invoice.customer_email, "customer_address": invoice.customer_address,
        "customer_tax_pin": invoice.customer_tax_pin,
        "customer_kind": invoice.customer_kind or (invoice.customer.kind if invoice.customer_id else Customer.Kind.INDIVIDUAL),
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date, "invoice_discount": invoice.invoice_discount or None,
        "payment_terms": invoice.payment_terms, "notes": invoice.notes,
    }


def _editor(request, invoice: Invoice | None, *, amend: bool = False):
    products = products_for(invoice)
    options = options_for(invoice)
    amend_form = AmendInvoiceForm(request.POST or None) if amend else None
    if request.method == "POST":
        header = InvoiceHeaderForm(request.POST, invoice=invoice)
        posted_kind = request.POST.get("customer_kind")
        kind = posted_kind if posted_kind in Customer.Kind.values else Customer.Kind.INDIVIDUAL
        lines = make_line_formset(data=request.POST, products=products, options=options, customer_kind=kind)
        forms_ok = header.is_valid() and lines.is_valid() and (amend_form is None or amend_form.is_valid())
        if forms_ok:
            try:
                if amend:
                    saved = services.amend_invoice(invoice, user=request.user, header=header.cleaned_data,
                                                   lines=lines.cleaned_lines(), reason=amend_form.cleaned_data["reason"])
                else:
                    saved = services.save_draft(invoice, user=request.user, header=header.cleaned_data,
                                                lines=lines.cleaned_lines())
            except InsufficientStockError as exc:
                header.add_error(None, exc.messages[0] + ". Record the stock first, or allow negative stock in Settings.")
            except ValidationError as exc:
                header.add_error(None, exc.messages[0])
            else:
                if amend:
                    messages.success(request, f"Invoice {saved.number} amended (revision {saved.revision}). "
                                              "Stock has been updated; the previous version is kept.")
                    return redirect("invoices:detail", pk=saved.pk)
                messages.success(request, "Draft saved." if invoice else "Draft created.")
                if "save_and_continue" in request.POST:
                    return redirect("invoices:edit", pk=saved.pk)
                return redirect("invoices:detail", pk=saved.pk)
        messages.error(request, "Not saved yet. Check the highlighted fields.")
    elif invoice is None:
        initial_customer = request.GET.get("customer", "")
        customer = Customer.objects.filter(pk=int(initial_customer), is_active=True).first() \
            if initial_customer.isdigit() else None
        header = InvoiceHeaderForm(initial=services.new_draft_initial(
            customer_kind=customer.kind if customer else Customer.Kind.INDIVIDUAL))
        if customer:
            header.initial["customer"] = customer.pk
        lines = make_line_formset(products=products, options=options)
    else:
        header = InvoiceHeaderForm(invoice=invoice, initial=_header_initial(invoice))
        unit_codes = {}
        for u in Unit.objects.all():
            unit_codes.setdefault(u.name, u.code)
            unit_codes[u.symbol] = u.code
        lines = make_line_formset(products=products, options=options,
                                  initial=[_line_initial(line, unit_codes) for line in invoice.lines.all()])

    settings = BusinessSettings.load()
    customers = header.fields["customer"].queryset
    tax_rate = invoice.tax_rate if amend else settings.effective_tax_rate()
    return render(request, "invoices/invoice_editor.html", {
        "invoice": invoice, "header": header, "lines": lines, "product_choices": products,
        "amend": amend, "amend_form": amend_form,
        "editor_data": {
            "products": _product_options(products, options, invoice if amend else None),
            "units": _units_data(),
            "customers": {
                str(c.pk): {"contact": c.contact_person, "phone": c.phone, "email": c.email,
                            "address": c.billing_address, "kind": c.kind}
                for c in customers
            },
            "paymentDefaults": {
                kind: dict(zip(("days", "terms"), settings.payment_defaults(kind))) for kind in Customer.Kind.values
            },
            "taxRate": str(tax_rate),
            "taxLabel": (invoice.tax_label if amend else (settings.tax_label if settings.tax_enabled else "")),
            "pricesIncludeTax": invoice.prices_include_tax if amend else settings.prices_include_tax,
            "currency": invoice.currency if amend else settings.currency,
            "allowNegativeStock": settings.allow_negative_stock,
        },
    })


@require_http_methods(["GET", "POST"])
@perm_required("invoices.add_invoice")
def invoice_create(request):
    return _editor(request, None)


@require_http_methods(["GET", "POST"])
@perm_required("invoices.change_invoice")
def invoice_edit(request, pk: int):
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status == Status.ISSUED:
        return redirect("invoices:amend", pk=pk)
    if invoice.status != Status.DRAFT:
        messages.info(request, f"Invoice {invoice.number} is cancelled, so it can't be edited.")
        return redirect("invoices:detail", pk=pk)
    return _editor(request, invoice)


@require_http_methods(["GET", "POST"])
@perm_required("invoices.amend_invoice")
def invoice_amend(request, pk: int):
    """Correct an issued invoice (e.g. add an omitted item). Same number; the
    previous version is archived and stock is adjusted."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status != Status.ISSUED:
        messages.info(request, "Only issued invoices can be amended." if invoice.status == Status.CANCELLED
                      else "This is still a draft: edit it directly.")
        return redirect("invoices:edit" if invoice.status == Status.DRAFT else "invoices:detail", pk=pk)
    return _editor(request, invoice, amend=True)


# --- Detail & actions ------------------------------------------------------------------

@require_GET
@perm_required("invoices.view_invoice")
def invoice_detail(request, pk: int):
    invoice = get_object_or_404(
        Invoice.objects.with_payment_totals().select_related("customer", "replaces", "issued_by", "cancelled_by", "created_by"),
        pk=pk,
    )
    lines = list(invoice.lines.select_related("product__stock_unit"))
    replacement = Invoice.objects.filter(replaces=invoice).first()
    settings = BusinessSettings.load()
    seller = invoice.business_snapshot if invoice.status != Status.DRAFT and invoice.business_snapshot else settings.snapshot()
    stock_warnings = []
    if invoice.status == Status.DRAFT:
        needed: dict = {}
        for line in lines:
            if line.product and line.product.track_stock and line.base_quantity:
                needed.setdefault(line.product, 0)
                needed[line.product] += line.base_quantity
        stock_warnings = [
            f"{p}: needs {format_in_display_unit(q, p.stock_unit)}, "
            f"{format_in_display_unit(p.stock_qty, p.stock_unit)} in stock"
            for p, q in needed.items() if p.stock_qty < q
        ]
    return render(request, "invoices/invoice_detail.html", {
        "invoice": invoice, "lines": lines, "seller": seller, "replacement": replacement,
        "has_line_discounts": any(line.line_discount for line in lines),
        "stock_warnings": stock_warnings, "allow_negative_stock": settings.allow_negative_stock,
        "business_ready": settings.is_configured,
        "revisions": list(invoice.revisions.select_related("created_by")),
        "allocations": list(invoice.allocations.select_related("payment__method").order_by("created_at", "pk")),
        "credit_payments": _credit_payments(invoice),
        "share": share_data(invoice, seller, invoice.balance) if invoice.status == Status.ISSUED else None,
    })


def _credit_payments(invoice):
    """The customer's payments with unused credit, offered on an unpaid invoice."""
    if invoice.status != Status.ISSUED or not invoice.customer_id or invoice.balance <= 0:
        return []
    from payments.models import Payment

    return [p for p in Payment.objects.filter(customer_id=invoice.customer_id, is_reversed=False)
            .with_allocation_totals().filter(unallocated__gt=0).select_related("method").order_by("payment_date", "pk")]


def _revision(pk: int, revision_no: int) -> tuple[InvoiceRevision, Invoice, list, object]:
    revision = get_object_or_404(InvoiceRevision.objects.select_related("invoice", "created_by"),
                                 invoice_id=pk, revision_no=revision_no)
    old, lines = revision.as_invoice()
    return revision, old, lines, revision.created_at


@require_GET
@perm_required("invoices.view_invoice")
def invoice_revision(request, pk: int, revision_no: int):
    """An earlier version of an amended invoice, read-only."""
    revision, old, lines, superseded = _revision(pk, revision_no)
    return render(request, "invoices/invoice_revision.html", {
        "revision": revision, "invoice": old, "current": revision.invoice, "lines": lines,
        "seller": old.business_snapshot or {}, "superseded": superseded,
        "has_line_discounts": any(line.line_discount for line in lines),
    })


@require_GET
@perm_required("invoices.view_invoice")
def invoice_revision_image(request, pk: int, revision_no: int):
    revision, old, lines, superseded = _revision(pk, revision_no)
    image = render_invoice_image(old, lines=lines, superseded_on=superseded)
    name = invoice_filename(old).replace(".png", f"_rev{revision_no}.png")
    response = FileResponse(image, content_type="image/png", as_attachment=request.GET.get("download") == "1",
                            filename=name)
    response["Cache-Control"] = "private, no-store"
    return response


@require_POST
@perm_required("invoices.issue_invoice")
def invoice_issue(request, pk: int):
    invoice = get_object_or_404(Invoice, pk=pk)
    try:
        invoice = services.issue_invoice(invoice, request.user)
    except InsufficientStockError as exc:
        messages.error(request, exc.messages[0] + ". Record the stock first, or allow negative stock in Settings.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(request, f"Invoice {invoice.number} issued. Stock has been updated.")
        if invoice.moved_payments:
            messages.info(request, f"{format_money(invoice.moved_payments, invoice.currency)} paid on the cancelled "
                                   f"invoice {invoice.replaces.number} has been applied to this one.")
    return redirect("invoices:detail", pk=pk)


@require_http_methods(["GET", "POST"])
@perm_required("invoices.cancel_invoice")
def invoice_cancel(request, pk: int):
    invoice = get_object_or_404(Invoice.objects.with_payment_totals(), pk=pk)
    if invoice.status != Status.ISSUED:
        messages.info(request, "Only issued invoices can be cancelled.")
        return redirect("invoices:detail", pk=pk)
    form = CancelInvoiceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            cancelled = services.cancel_invoice(invoice, request.user, form.cleaned_data["reason"])
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            messages.success(request, f"Invoice {invoice.number} cancelled. Its stock has been returned.")
            if cancelled.released_payments:
                messages.info(request, f"{format_money(cancelled.released_payments, invoice.currency)} already paid "
                                       "is now credit; it goes onto the replacement when you issue it.")
            if "replace" in request.POST and request.user.has_perm("invoices.add_invoice"):
                draft = services.create_replacement_draft(invoice, request.user)
                messages.info(request, f"This draft replaces {invoice.number}. Correct it, then issue it.")
                return redirect("invoices:edit", pk=draft.pk)
            return redirect("invoices:detail", pk=pk)
    return render(request, "invoices/invoice_cancel.html", {"invoice": invoice, "form": form})


@require_POST
@perm_required("invoices.add_invoice")
def invoice_replace(request, pk: int):
    invoice = get_object_or_404(Invoice, pk=pk)
    try:
        draft = services.create_replacement_draft(invoice, request.user)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect("invoices:detail", pk=pk)
    messages.info(request, f"This draft replaces {invoice.number}. Correct it, then issue it.")
    return redirect("invoices:edit", pk=draft.pk)


@require_POST
@perm_required("invoices.add_invoice")
def invoice_duplicate(request, pk: int):
    source = get_object_or_404(Invoice, pk=pk)
    draft = services.duplicate_invoice(source, request.user)
    messages.success(request, f"New draft copied from {source.number or 'the draft'}, with today's prices. Check it before issuing.")
    return redirect("invoices:edit", pk=draft.pk)


@require_POST
@perm_required("invoices.delete_invoice")
def invoice_delete(request, pk: int):
    invoice = get_object_or_404(Invoice, pk=pk)
    try:
        services.delete_draft(invoice, request.user)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect("invoices:detail", pk=pk)
    messages.success(request, "Draft deleted.")
    return redirect("invoices:list")


@require_GET
@perm_required("invoices.view_invoice")
def invoice_image(request, pk: int):
    invoice = get_object_or_404(Invoice, pk=pk)
    image = render_invoice_image(invoice)
    response = FileResponse(image, content_type="image/png",
                            as_attachment=request.GET.get("download") == "1",
                            filename=invoice_filename(invoice))
    response["Cache-Control"] = "private, no-store"
    return response
