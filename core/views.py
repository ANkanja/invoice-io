import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import SuspiciousFileOperation
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.utils._os import safe_join
from django.views.decorators.http import require_GET, require_http_methods

from inventory.models import StockMovement
from products.models import Product

from .forms import BusinessSettingsForm
from .models import AuditLog, BusinessSettings


@require_GET
def dashboard(request):
    business = BusinessSettings.load()
    low_stock = Product.objects.low_stock().select_related("category").order_by("stock_qty", "name")
    context = {
        "business_ready": business.is_configured,
        "product_count": Product.objects.active().count(),
        "has_stock": StockMovement.objects.exists(),
        "tracked_count": Product.objects.active().filter(track_stock=True).count(),
        "low_stock": low_stock[:20],
        "low_stock_total": low_stock.count(),
    }
    return render(request, "core/dashboard.html", context)


@require_http_methods(["GET", "POST"])
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
