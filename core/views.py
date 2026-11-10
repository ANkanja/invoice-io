import datetime
import mimetypes
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import SuspiciousFileOperation
from django.db import transaction
from django.db.models import Sum
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils._os import safe_join
from django.views.decorators.http import require_GET, require_http_methods

from inventory.models import StockMovement
from posters.services import upcoming_occasions
from products.models import Product
from products.units import format_in_display_unit

from .forms import BusinessSettingsForm
from .permissions import perm_required
from .models import AuditLog, BusinessSettings


def _period_starts(today):
    return {
        "today": today,
        "week": today - datetime.timedelta(days=today.weekday()),  # Monday
        "month": today.replace(day=1),
    }


def _money_dashboard(today) -> dict:
    """Invoiced vs received, what's owed, recent activity, best sellers."""
    from invoices.models import Invoice, InvoiceLine
    from payments.models import Payment

    zero = Decimal("0.00")
    issued = Invoice.objects.issued()
    received = Payment.objects.active()
    periods = []
    for key, label in [("today", "Today"), ("week", "This week"), ("month", "This month")]:
        start = _period_starts(today)[key]
        periods.append({
            "label": label,
            "invoiced": issued.filter(issue_date__gte=start, issue_date__lte=today).aggregate(s=Sum("total"))["s"] or zero,
            "received": received.filter(payment_date__gte=start, payment_date__lte=today).aggregate(s=Sum("amount"))["s"] or zero,
        })
    outstanding = issued.with_payment_totals().filter(balance__gt=0)
    month_start = _period_starts(today)["month"]
    sellers = list(
        InvoiceLine.objects.filter(invoice__status=Invoice.Status.ISSUED, invoice__issue_date__gte=month_start,
                                   invoice__issue_date__lte=today, product__isnull=False)
        .values("product").annotate(revenue=Sum("line_total"), qty=Sum("base_quantity"))
        .order_by("-revenue")[:5]
    )
    products = Product.objects.select_related("stock_unit").in_bulk([row["product"] for row in sellers])
    for row in sellers:
        product = products[row["product"]]
        row["product"] = product
        row["qty_display"] = format_in_display_unit(row["qty"], product.stock_unit) if row["qty"] is not None else ""
    return {
        "periods": periods,
        "outstanding": outstanding.money_totals(),
        "overdue": outstanding.filter(due_date__lt=today).money_totals(),
        "recent_invoices": Invoice.objects.exclude(status=Invoice.Status.DRAFT).with_payment_totals()
                                  .order_by("-issued_at", "-id")[:6],
        "draft_count": Invoice.objects.filter(status=Invoice.Status.DRAFT).count(),
        "recent_payments": Payment.objects.select_related("method").order_by("-payment_date", "-id")[:6],
        "best_sellers": sellers,
    }


@require_GET
def dashboard(request):
    business = BusinessSettings.load()
    today = timezone.localdate()
    low_stock = Product.objects.low_stock().select_related("category", "stock_unit").order_by("stock_qty", "name")
    context = {
        "business_ready": business.is_configured,
        "product_count": Product.objects.active().count(),
        "has_stock": StockMovement.objects.exists(),
        "tracked_count": Product.objects.active().filter(track_stock=True).count(),
        "low_stock": low_stock[:20],
        "low_stock_total": low_stock.count(),
        "today": today,
        "show_money": request.user.has_perm("invoices.view_invoice") and request.user.has_perm("payments.view_payment"),
    }
    if context["show_money"]:
        context.update(_money_dashboard(today))
    if request.user.has_perm("posters.view_occasion"):
        context["upcoming_occasions"] = upcoming_occasions(today, days=30)[:6]
    return render(request, "core/dashboard.html", context)


@require_http_methods(["GET", "POST"])
@perm_required("core.change_businesssettings")
def business_settings(request):
    instance = BusinessSettings.load()
    before = {f: getattr(instance, f) for f in BusinessSettingsForm.Meta.fields}
    form = BusinessSettingsForm(request.POST or None, request.FILES or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            saved = form.save()
            changes = {
                f: [str(before[f]), str(getattr(saved, f))]
                for f in form.changed_data
                if str(before[f]) != str(getattr(saved, f))
            }
            if changes:
                AuditLog.record(actor=request.user, action="settings.updated", obj=saved, changes=changes)
        messages.success(request, "Settings saved.")
        return redirect("core:settings")
    if request.method == "POST":
        messages.error(request, "Settings not saved. Fix the highlighted fields.")
    return render(request, "core/settings.html", {"form": form, "max_upload_mb": settings.MAX_UPLOAD_MB})


@require_GET
def protected_media(request, path: str):
    """Serve an uploaded file to a logged-in user (login enforced by
    LoginRequiredMiddleware). Rejects path traversal such as ../../.env."""
    try:
        full_path = Path(safe_join(settings.MEDIA_ROOT, path))
    except (SuspiciousFileOperation, ValueError):
        raise Http404("File not found")
    if not full_path.is_file():
        raise Http404("File not found")
    content_type, _ = mimetypes.guess_type(full_path.name)
    response = FileResponse(full_path.open("rb"), content_type=content_type or "application/octet-stream")
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response
