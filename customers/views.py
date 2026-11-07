from decimal import Decimal

from django.contrib import messages
from django.db.models import Q, Sum, Value
from django.db.models.functions import Replace
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.pagination import paginate
from core.permissions import perm_required
from invoices.models import Invoice
from payments.models import Payment
from payments.services import customer_credit

from . import services
from .forms import CustomerFilterForm, CustomerForm
from .models import Customer


def _digits_only(field: str):
    expr = field
    for ch in (" ", "-", "(", ")", "+"):
        expr = Replace(expr, Value(ch), Value(""))
    return expr


@require_GET
@perm_required("customers.view_customer")
def customer_list(request):
    form = CustomerFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    qs = Customer.objects.all()
    status = data.get("status") or "active"
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "archived":
        qs = qs.filter(is_active=False)
    if kind := data.get("kind"):
        qs = qs.filter(kind=kind)
    if q := (data.get("q") or "").strip():
        cond = Q(name__icontains=q) | Q(contact_person__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
        digits = "".join(ch for ch in q if ch.isdigit())
        if len(digits) >= 4:
            # Compare digits only, ignoring spaces/dashes/brackets, and match on the
            # last 9 digits so 0712 345678 and +254 712 345678 are the same number.
            qs = qs.annotate(phone_digits=_digits_only("phone"))
            cond |= Q(phone_digits__contains=digits[-9:])
        qs = qs.filter(cond)
    filtered = bool(data.get("q") or data.get("kind")) or status != "active"
    return render(request, "customers/customer_list.html", {
        "filter_form": form,
        "page": paginate(request, qs.order_by("name")),
        "filtered": filtered,
        "has_any": Customer.objects.exists(),
    })


@require_GET
@perm_required("customers.view_customer")
def customer_detail(request, pk: int):
    customer = get_object_or_404(Customer, pk=pk)
    invoices = (
        Invoice.objects.filter(customer=customer).exclude(status=Invoice.Status.DRAFT)
        .with_payment_totals().order_by("-issue_date", "-id")
    )
    issued = invoices.filter(status=Invoice.Status.ISSUED)
    today = timezone.localdate()
    totals = issued.money_totals()
    overdue = issued.filter(balance__gt=0, due_date__lt=today).money_totals()
    payments = (Payment.objects.filter(customer=customer).with_allocation_totals().select_related("method")
                .order_by("-payment_date", "-id"))
    return render(request, "customers/customer_detail.html", {
        "customer": customer,
        "page": paginate(request, invoices, per_page=20),
        "outstanding": totals["balance"],
        "overdue": overdue["balance"],
        "invoiced_total": totals["total"],
        "paid_total": totals["paid"],
        "credit": customer_credit(customer),
        "payments": payments[:10],
        "payment_count": payments.count(),
        "today": today,
    })


def _warn_duplicates(request, form):
    dupes = form.possible_duplicates()
    if dupes:
        names = ", ".join(str(c) for c in dupes)
        messages.warning(request, f"Check for duplicates: {names} has the same name or phone number.")


@require_http_methods(["GET", "POST"])
@perm_required("customers.add_customer")
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = services.save_customer(form, request.user)
        messages.success(request, f"Added {customer}.")
        _warn_duplicates(request, form)
        return redirect("customers:detail", pk=customer.pk)
    return render(request, "customers/customer_form.html", {"form": form, "is_new": True})


@require_http_methods(["GET", "POST"])
@perm_required("customers.change_customer")
def customer_edit(request, pk: int):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        customer = services.save_customer(form, request.user)
        messages.success(request, f"Saved changes to {customer}.")
        _warn_duplicates(request, form)
        return redirect("customers:detail", pk=customer.pk)
    return render(request, "customers/customer_form.html", {"form": form, "is_new": False, "customer": customer})


@require_POST
@perm_required("customers.change_customer")
def customer_set_active(request, pk: int, active: bool):
    customer = get_object_or_404(Customer, pk=pk)
    services.set_customer_active(customer, active, request.user)
    if active:
        messages.success(request, f"Restored {customer}.")
    else:
        messages.success(request, f"Archived {customer}. Their invoices and history are kept.")
    return redirect("customers:detail", pk=pk)
