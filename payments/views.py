from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.money import format_money
from core.pagination import paginate
from core.permissions import perm_required
from customers.models import Customer
from invoices.models import Invoice

from . import services
from .forms import ApplyCreditForm, PaymentFilterForm, PaymentForm, ReversePaymentForm
from .models import Payment, PaymentMethod
from .pdf import receipt_filename, render_receipt_pdf


def open_invoices(customer=None, invoice=None):
    """Issued invoices with money still due: the customer's (oldest first), or
    just the one invoice for a one-time customer."""
    qs = Invoice.objects.issued().with_payment_totals().filter(balance__gt=0)
    if customer is not None:
        qs = qs.filter(customer=customer)
    elif invoice is not None:
        qs = qs.filter(pk=invoice.pk)
    else:
        return []
    return sorted(qs, key=lambda i: (i.due_date or i.issue_date, i.issue_date, i.pk))


def _summary(payment: Payment) -> str:
    applied = list(payment.allocations.filter(released_at__isnull=True).select_related("invoice"))
    parts = [f"{format_money(a.amount)} to {a.invoice.number}" for a in applied]
    text = f"Payment of {format_money(payment.amount)} recorded"
    if parts:
        text += ": " + ", ".join(parts)
    credit = payment.get_unallocated()
    if credit > 0:
        text += f". {format_money(credit)} kept as credit" + (f" for {payment.customer}" if payment.customer else "")
    return text + "."


def _safe_next(request, fallback: str) -> str:
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return fallback


# --- List ----------------------------------------------------------------------------

@require_GET
@perm_required("payments.view_payment")
def payment_list(request):
    form = PaymentFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    qs = Payment.objects.with_allocation_totals().select_related("method", "customer")
    if q := (data.get("q") or "").strip():
        qs = qs.filter(Q(payer_name__icontains=q) | Q(reference__icontains=q)
                       | Q(allocations__invoice__number__icontains=q)).distinct()
    if data.get("method"):
        qs = qs.filter(method=data["method"])
    if data.get("customer"):
        qs = qs.filter(customer=data["customer"])
    if data.get("date_from"):
        qs = qs.filter(payment_date__gte=data["date_from"])
    if data.get("date_to"):
        qs = qs.filter(payment_date__lte=data["date_to"])
    state = data.get("state") or ""
    if state == "reversed":
        qs = qs.filter(is_reversed=True)
    elif state == "credit":
        qs = qs.filter(is_reversed=False, unallocated__gt=0)
    received = Payment.objects.filter(pk__in=qs.values("pk"), is_reversed=False).aggregate(s=Sum("amount"))["s"]
    filtered = any(data.get(k) for k in ("q", "method", "customer", "date_from", "date_to", "state"))
    return render(request, "payments/payment_list.html", {
        "filter_form": form, "page": paginate(request, qs.order_by("-payment_date", "-id")),
        "filtered": filtered, "received": received or Decimal("0.00"), "has_any": Payment.objects.exists(),
    })


# --- Record ------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
@perm_required("payments.add_payment")
def payment_create(request):
    """Record money received. Opened from an invoice (?invoice=), a customer
    (?customer=) or on its own; the customer's open invoices are listed so the
    payment can be split across them."""
    invoice = None
    if (inv_id := request.GET.get("invoice", "")).isdigit():
        invoice = Invoice.objects.issued().with_payment_totals().filter(pk=int(inv_id)).first()
    raw_customer = request.POST.get("customer") if request.method == "POST" else request.GET.get("customer")
    customer = None
    if raw_customer and str(raw_customer).isdigit():
        customer = Customer.objects.filter(pk=int(raw_customer), is_active=True).first()
    elif request.method == "GET" and invoice is not None and invoice.customer_id and invoice.customer.is_active:
        customer = invoice.customer
    invoices = open_invoices(customer, invoice if customer is None else None)

    if request.method == "POST":
        form = PaymentForm(request.POST, invoices=invoices)
        if form.is_valid():
            d = form.cleaned_data
            try:
                payment = services.record_payment(
                    user=request.user, customer=d["customer"], payer_name=d["payer_name"], amount=d["amount"],
                    payment_date=d["payment_date"], method=d["method"], reference=d["reference"], notes=d["notes"],
                    allocations=[services.Allocation(inv, amount) for inv, amount in d["allocations"]],
                    submission_token=d["token"],
                )
            except services.DuplicateSubmission:
                messages.info(request, "That payment was already recorded, so it wasn't added again.")
                return redirect("payments:list")
            except ValidationError as exc:
                form.add_error(None, exc.messages[0])
            else:
                messages.success(request, _summary(payment))
                fallback = reverse("payments:detail", args=[payment.pk])
                return redirect(_safe_next(request, fallback))
        messages.error(request, "Payment not recorded yet. Check the highlighted fields.")
    else:
        initial = {}
        if customer is not None:
            initial["customer"] = customer
        elif invoice is not None:
            initial["payer_name"] = invoice.customer_name
        if invoice is not None:
            initial["amount"] = invoice.balance
            initial[f"alloc_{invoice.pk}"] = invoice.balance
        if (m := PaymentMethod.objects.filter(is_active=True).first()) is not None:
            initial["method"] = m
        form = PaymentForm(initial=initial, invoices=invoices)

    credit = services.customer_credit(customer) if customer is not None else Decimal("0")
    return render(request, "payments/payment_form.html", {
        "form": form, "invoice": invoice, "customer": customer, "credit": credit, "today": timezone.localdate(),
        "next": _safe_next(request, ""),
        "methods": {str(m.pk): {"needsRef": m.requires_reference} for m in PaymentMethod.objects.filter(is_active=True)},
    })


# --- Detail, apply credit, reverse, receipt ------------------------------------------------

def _get_payment(pk) -> Payment:
    return get_object_or_404(
        Payment.objects.with_allocation_totals().select_related("method", "customer", "created_by", "reversed_by"), pk=pk
    )


@require_GET
@perm_required("payments.view_payment")
def payment_detail(request, pk: int):
    payment = _get_payment(pk)
    credit = payment.get_unallocated()
    apply_form = None
    if credit > 0 and payment.customer_id and request.user.has_perm("payments.add_payment"):
        apply_form = ApplyCreditForm(invoices=open_invoices(payment.customer), credit=credit)
    return render(request, "payments/payment_detail.html", {
        "payment": payment, "credit": credit, "apply_form": apply_form, "today": timezone.localdate(),
        "allocations": payment.allocations.select_related("invoice", "released_by").order_by("created_at", "pk"),
    })


@require_POST
@perm_required("payments.add_payment")
def payment_apply(request, pk: int):
    """Apply a payment's credit to the customer's open invoices (from the payment page)."""
    payment = _get_payment(pk)
    credit = payment.get_unallocated()
    form = ApplyCreditForm(request.POST, invoices=open_invoices(payment.customer) if payment.customer else [],
                           credit=credit)
    if form.is_valid():
        try:
            services.apply_credit(payment, [services.Allocation(i, a) for i, a in form.cleaned_data["allocations"]],
                                  request.user)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            messages.success(request, "Credit applied.")
            return redirect("payments:detail", pk=pk)
    else:
        messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
    return redirect("payments:detail", pk=pk)


@require_POST
@perm_required("payments.add_payment")
def apply_to_invoice(request, pk: int, invoice_pk: int):
    """One click on an invoice page: use this payment's credit for the invoice
    (as much as is due, or as much credit as there is)."""
    payment = _get_payment(pk)
    invoice = get_object_or_404(Invoice.objects.with_payment_totals(), pk=invoice_pk)
    amount = min(payment.get_unallocated(), invoice.balance)
    try:
        if amount <= 0:
            raise ValidationError("There's nothing to apply.")
        services.apply_credit(payment, [services.Allocation(invoice, amount)], request.user)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(request, f"{format_money(amount, invoice.currency)} of credit applied to {invoice.number}.")
    return redirect("invoices:detail", pk=invoice_pk)


@require_http_methods(["GET", "POST"])
@perm_required("payments.reverse_payment")
def payment_reverse(request, pk: int):
    payment = _get_payment(pk)
    if payment.is_reversed:
        messages.info(request, "This payment is already reversed.")
        return redirect("payments:detail", pk=pk)
    form = ReversePaymentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.reverse_payment(payment, request.user, form.cleaned_data["reason"])
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            messages.success(request, f"Payment of {format_money(payment.amount)} reversed. "
                                      "The invoices it paid show the money as owed again.")
            return redirect("payments:detail", pk=pk)
    return render(request, "payments/payment_reverse.html", {
        "payment": payment, "form": form,
        "allocations": payment.allocations.filter(released_at__isnull=True).select_related("invoice"),
    })


@require_GET
@perm_required("payments.view_payment")
def payment_receipt(request, pk: int):
    payment = _get_payment(pk)
    pdf = render_receipt_pdf(payment)
    response = FileResponse(pdf, content_type="application/pdf", as_attachment=request.GET.get("download") == "1",
                            filename=receipt_filename(payment))
    response["Cache-Control"] = "private, no-store"
    return response
