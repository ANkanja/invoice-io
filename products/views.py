from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.pagination import paginate
from core.permissions import perm_required

from . import services
from .forms import CategoryForm, ProductFilterForm, ProductForm, SellingOptionForm, units_payload
from .models import Category, Product, SellingOption


def filter_products(params) -> tuple[ProductFilterForm, "QuerySet[Product]"]:
    form = ProductFilterForm(params or None)
    data = form.cleaned_data if form.is_valid() else {}
    qs = Product.objects.select_related("category")

    status = data.get("status") or "active"
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "archived":
        qs = qs.filter(is_active=False)

    if q := (data.get("q") or "").strip():
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(options__label__icontains=q)).distinct()

    category = data.get("category") or ""
    if category == "none":
        qs = qs.filter(category__isnull=True)
    elif category.isdigit():
        qs = qs.filter(category_id=int(category))

    stock = data.get("stock") or ""
    if stock == "low":
        qs = qs.filter(pk__in=Product.objects.low_stock().values("pk"))
    elif stock == "out":
        qs = qs.filter(track_stock=True, stock_qty__lte=0)

    return form, qs.select_related("stock_unit").prefetch_related("options").order_by("name")


@require_GET
@perm_required("products.view_product")
def product_list(request):
    form, qs = filter_products(request.GET)
    return render(request, "products/product_list.html", {
        "filter_form": form,
        "page": paginate(request, qs),
        "filtered": any(request.GET.get(k) for k in ("q", "category", "stock")) or request.GET.get("status") not in (None, "", "active"),
        "has_any": Product.objects.exists(),
    })


@require_GET
@perm_required("products.view_product")
def product_detail(request, pk: int):
    product = get_object_or_404(Product.objects.select_related("category", "stock_unit"), pk=pk)
    movements = product.stock_movements.select_related("created_by", "invoice_line__invoice")[:15]
    options = product.options.select_related("package_unit", "price_unit").order_by("-is_active", "-is_default", "sort_order", "pk")
    return render(request, "products/product_detail.html", {"product": product, "movements": movements, "options": options})


def _product_form_page(request, form, *, is_new, product=None):
    return render(request, "products/product_form.html", {
        "form": form, "is_new": is_new, "product": product, "units_data": units_payload(),
    })


@require_http_methods(["GET", "POST"])
@perm_required("products.add_product")
def product_create(request):
    form = ProductForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        product = services.save_product(form, request.user)
        messages.success(request, f"Added {product}.")
        if "add_another" in request.POST:
            return redirect("products:create")
        return redirect("products:detail", pk=product.pk)
    return _product_form_page(request, form, is_new=True)


@require_http_methods(["GET", "POST"])
@perm_required("products.change_product")
def product_edit(request, pk: int):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, request.FILES or None, instance=product)
    if request.method == "POST" and form.is_valid():
        product = services.save_product(form, request.user)
        messages.success(request, f"Saved changes to {product}.")
        return redirect("products:detail", pk=product.pk)
    return _product_form_page(request, form, is_new=False, product=product)


@require_http_methods(["GET", "POST"])
@perm_required("products.change_product")
def option_edit(request, pk: int, option_pk: int | None = None):
    product = get_object_or_404(Product.objects.select_related("stock_unit"), pk=pk)
    option = get_object_or_404(SellingOption, pk=option_pk, product=product) if option_pk else None
    form = SellingOptionForm(request.POST or None, product=product, instance=option)
    if request.method == "POST" and form.is_valid() and form.option_values:
        saved = services.save_option(product, form.option_values, request.user, instance=option,
                                     is_default=form.cleaned_data["is_default"], is_active=form.cleaned_data["is_active"])
        messages.success(request, f"Saved “{saved.label}”. Existing invoices keep their own prices.")
        return redirect("products:detail", pk=product.pk)
    return render(request, "products/option_form.html", {
        "form": form, "product": product, "option": option, "units_data": units_payload(),
    })


@require_POST
@perm_required("products.change_product")
def product_archive(request, pk: int):
    product = get_object_or_404(Product, pk=pk)
    services.set_product_active(product, False, request.user)
    messages.success(request, f"Archived {product}. It no longer appears when adding to invoices; past invoices are unchanged.")
    return redirect("products:detail", pk=pk)


@require_POST
@perm_required("products.change_product")
def product_restore(request, pk: int):
    product = get_object_or_404(Product, pk=pk)
    services.set_product_active(product, True, request.user)
    messages.success(request, f"Restored {product}.")
    return redirect("products:detail", pk=pk)


# --- Categories ---------------------------------------------------------------

@require_http_methods(["GET", "POST"])
@perm_required("products.view_category")
def category_list(request):
    form = CategoryForm(request.POST or None)
    if request.method == "POST":
        if not request.user.has_perm("products.add_category"):
            messages.error(request, "You don't have permission to add categories.")
            return redirect("products:categories")
        if form.is_valid():
            category = services.save_category(form, request.user)
            messages.success(request, f"Added category {category}.")
            return redirect("products:categories")
    categories = Category.objects.annotate(
        product_count=Count("products", filter=Q(products__is_active=True))
    ).order_by("-is_active", "name")
    return render(request, "products/category_list.html", {"form": form, "categories": categories})


@require_http_methods(["GET", "POST"])
@perm_required("products.change_category")
def category_edit(request, pk: int):
    category = get_object_or_404(Category, pk=pk)
    form = CategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        services.save_category(form, request.user)
        messages.success(request, "Category renamed.")
        return redirect("products:categories")
    return render(request, "products/category_form.html", {"form": form, "category": category})


@require_POST
@perm_required("products.change_category")
def category_set_active(request, pk: int, active: bool):
    category = get_object_or_404(Category, pk=pk)
    services.set_category_active(category, active, request.user)
    messages.success(request, f"{'Restored' if active else 'Archived'} category {category}.")
    return redirect("products:categories")
