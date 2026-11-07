import uuid

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from core.money import format_quantity
from core.pagination import paginate
from core.permissions import perm_required
from products.models import Category, Product, Unit
from products.pricing import stock_entry_choices
from products.units import format_in_display_unit

from . import services
from .forms import (
    MovementFilterForm, ReturnForm, StockCountForm, StockFilterForm, StockInForm, StockOutForm, StockTakeForm,
    default_measure, stock_products,
)
from .models import StockMovement


@require_GET
@perm_required("products.view_product", "inventory.view_stockmovement")
def stock_overview(request):
    form = StockFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    tracked = stock_products()
    qs = tracked
    if q := (data.get("q") or "").strip():
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q))
    if (cat := data.get("category") or "").isdigit():
        qs = qs.filter(category_id=int(cat))
    level = data.get("level") or ""
    if level == "low":
        qs = qs.filter(pk__in=Product.objects.low_stock().values("pk"))
    elif level == "out":
        qs = qs.filter(stock_qty__lte=0)
    return render(request, "inventory/overview.html", {
        "filter_form": form,
        "page": paginate(request, qs, per_page=50),
        "filtered": bool(data.get("q") or data.get("category") or level),
        "tracked_count": tracked.count(),
        "low_count": Product.objects.low_stock().count(),
        "out_count": tracked.filter(stock_qty__lte=0).count(),
    })


def _product_data():
    """Per product: current stock and the units/packs it can be entered in, for
    the stock forms' unit list and live hints (TypeScript)."""
    products = {}
    for p in stock_products():
        products[str(p.pk)] = {
            "stock": str(p.stock_qty), "stockUnit": p.stock_unit.code, "stockDisplay": p.stock_display,
            "default": default_measure(p),
            "choices": [[value, label, str(factor)] for value, label, factor in stock_entry_choices(p)],
        }
    units = {u.code: {"symbol": u.symbol, "name": u.name, "plural": u.plural, "dimension": u.dimension,
                      "factor": str(u.factor)} for u in Unit.objects.all()}
    return {"products": products, "units": units}


def _amount(product, qty) -> str:
    return format_in_display_unit(qty, product.stock_unit)


def _entered_text(d) -> str:
    qty, label = d["entered"]
    return f"{format_quantity(qty)} {label}"


def _initial_product(request):
    pk = request.GET.get("product", "")
    return stock_products().filter(pk=pk).first() if pk.isdigit() else None


def _movement_form_view(request, *, form_class, title, intro, submit_label, record):
    """Shared flow for stock in / stock out / count."""
    initial = {}
    if product := _initial_product(request):
        initial["product"] = product
    form = form_class(request.POST or None, initial=initial)

    if request.method == "POST" and form.is_valid():
        product = form.cleaned_data["product"]
        try:
            message = record(form, request.user)
        except services.DuplicateSubmission:
            messages.info(request, "That entry was already recorded, so it wasn't added again.")
            return redirect("products:detail", pk=product.pk)
        except services.StockError as exc:
            form.add_error(None, exc.messages[0])
        else:
            messages.success(request, message)
            if "add_another" in request.POST:
                return redirect(request.path)
            return redirect("products:detail", pk=product.pk)

    return render(request, "inventory/movement_form.html", {
        "form": form, "title": title, "intro": intro, "submit_label": submit_label,
        "product_data": _product_data(), "has_products": stock_products().exists(),
    })


@require_http_methods(["GET", "POST"])
@perm_required("inventory.add_stockmovement")
def stock_in(request):
    def record(form, user):
        d = form.cleaned_data
        m = services.record_stock_in(d["product"], d["base_quantity"], user, unit_cost=d.get("unit_cost"),
                                     reference=d.get("reference", ""), notes=d.get("notes", ""),
                                     submission_token=d["token"], entered=d["entered"])
        return f"Added {_entered_text(d)} of {m.product}. Now {_amount(m.product, m.balance_after)} in stock."

    return _movement_form_view(request, form_class=StockInForm, title="Stock in",
                               intro="Record stock arriving: a delivery, a production batch, or your opening stock.",
                               submit_label="Record stock in", record=record)


@require_http_methods(["GET", "POST"])
@perm_required("inventory.add_stockmovement")
def stock_out(request):
    def record(form, user):
        d = form.cleaned_data
        m = services.record_stock_out(d["product"], d["base_quantity"], user, reason=d["reason"],
                                      reference=d.get("reference", ""), notes=d.get("notes", ""),
                                      submission_token=d["token"], entered=d["entered"])
        return f"Removed {_entered_text(d)} of {m.product}. Now {_amount(m.product, m.balance_after)} in stock."

    return _movement_form_view(request, form_class=StockOutForm, title="Stock out",
                               intro="Record stock that left without a sale: damaged, expired, used, given as samples. "
                                     "Sales are deducted automatically when you issue an invoice.",
                               submit_label="Record stock out", record=record)


@require_http_methods(["GET", "POST"])
@perm_required("inventory.add_stockmovement")
def stock_count(request):
    def record(form, user):
        d = form.cleaned_data
        m = services.record_stock_count(d["product"], d["base_quantity"], user, notes=d.get("notes", ""),
                                        submission_token=d["token"], entered=d["entered"])
        if m is None:
            return f"Count matches: {_entered_text(d)} of {d['product']}. Nothing to adjust."
        sign = "+" if m.quantity > 0 else "-"
        return (f"Adjusted {m.product} by {sign}{_amount(m.product, abs(m.quantity))} "
                f"to {_amount(m.product, m.balance_after)}.")

    return _movement_form_view(request, form_class=StockCountForm, title="Count stock",
                               intro="Enter what you physically counted for one product. For many products at once, use Stock take.",
                               submit_label="Save count", record=record)


@require_http_methods(["GET", "POST"])
@perm_required("inventory.add_stockmovement")
def stock_return(request):
    def record(form, user):
        d = form.cleaned_data
        m = services.record_return(d["product"], d["base_quantity"], user, reference=d.get("reference", ""),
                                   notes=d.get("notes", ""), submission_token=d["token"], entered=d["entered"])
        return f"Returned {_entered_text(d)} of {m.product} to stock. Now {_amount(m.product, m.balance_after)}."

    return _movement_form_view(request, form_class=ReturnForm, title="Customer return",
                               intro="Goods a customer brought back that go back on the shelf. To take a whole item "
                                     "off an invoice, amend the invoice instead; its stock is adjusted automatically.",
                               submit_label="Record return", record=record)


@require_http_methods(["GET", "POST"])
@perm_required("inventory.add_stockmovement")
def stock_take(request):
    products = stock_products()
    category = request.GET.get("category", "")
    if category.isdigit():
        products = products.filter(category_id=int(category))
    form = StockTakeForm(request.POST or None, products=products)

    if request.method == "POST" and form.is_valid():
        token: uuid.UUID = form.cleaned_data["token"]
        notes = form.cleaned_data["notes"] or "Stock take"
        try:
            with transaction.atomic():
                adjusted = 0
                counts = form.counts()
                for pk, (counted, entered) in counts.items():
                    product = next(p for p in form.products if p.pk == pk)
                    m = services.record_stock_count(product, counted, request.user, notes=notes,
                                                    submission_token=uuid.uuid5(token, str(pk)), entered=entered)
                    adjusted += m is not None
        except services.DuplicateSubmission:
            messages.info(request, "That stock take was already saved, so it wasn't applied again.")
            return redirect("inventory:overview")
        except services.StockError as exc:
            form.add_error(None, exc.messages[0])
        else:
            messages.success(request, f"Stock take saved: {len(counts)} counted, {adjusted} adjusted, "
                                      f"{len(counts) - adjusted} already correct.")
            return redirect(reverse("inventory:movements") + "?kind=adjustment")

    return render(request, "inventory/stock_take.html", {
        "form": form, "categories": Category.objects.filter(is_active=True).order_by("name"),
        "selected_category": category,
    })


@require_GET
@perm_required("inventory.view_stockmovement")
def movement_list(request):
    form = MovementFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    qs = StockMovement.objects.select_related("product__stock_unit", "created_by", "invoice_line__invoice")
    if data.get("product"):
        qs = qs.filter(product=data["product"])
    if data.get("kind"):
        qs = qs.filter(kind=data["kind"])
    if data.get("date_from"):
        qs = qs.filter(created_at__date__gte=data["date_from"])
    if data.get("date_to"):
        qs = qs.filter(created_at__date__lte=data["date_to"])
    filtered = any(data.get(k) for k in ("product", "kind", "date_from", "date_to"))
    return render(request, "inventory/movement_list.html", {
        "filter_form": form, "page": paginate(request, qs, per_page=50), "filtered": filtered,
    })
