from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.money import quantize
from core.permissions import perm_required

from . import services
from .forms import AddItemForm, AddProductForm, NewPlanForm, PlanForm, PurchaseForm, ReverseForm
from .models import RestockLine, RestockPlan, RestockPurchase


@require_http_methods(["GET", "POST"])
@perm_required("restock.view_restockplan")
def plan_list(request):
    taken = set(RestockPlan.objects.values_list("month", flat=True))
    can_add = request.user.has_perm("restock.add_restockplan")
    form = NewPlanForm(request.POST or None, taken=taken) if can_add else None
    if request.method == "POST":
        if not can_add:
            return redirect("restock:list")
        if form.is_valid():
            try:
                plan = services.create_plan(month=form.cleaned_data["month"], budget=form.cleaned_data.get("budget"),
                                            user=request.user)
            except ValidationError as exc:
                form.add_error("month", exc)
            else:
                messages.success(request, f"Plan for {plan.month:%B %Y} started with {plan.lines.count()} items. "
                                          "Check the quantities and prices.")
                return redirect("restock:detail", pk=plan.pk)
    plans = [services.plan_figures(p) for p in RestockPlan.objects.all()[:24]]
    return render(request, "restock/plan_list.html", {"form": form, "plans": plans})


@require_http_methods(["GET", "POST"])
@perm_required("restock.view_restockplan")
def plan_detail(request, pk: int):
    plan = get_object_or_404(RestockPlan, pk=pk)
    figures = services.plan_figures(plan)
    can_edit = request.user.has_perm("restock.change_restockplan")
    lines = [f.line for f in figures.lines]
    form = PlanForm(request.POST or None, plan=plan, lines=lines) if can_edit else None
    if request.method == "POST":
        if not can_edit:
            return redirect("restock:detail", pk=plan.pk)
        if form.is_valid():
            try:
                services.save_plan(plan, user=request.user, budget=form.cleaned_data.get("budget"),
                                   notes=form.cleaned_data.get("notes", ""), lines=form.line_values())
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Plan saved.")
                return redirect("restock:detail", pk=plan.pk)
        messages.error(request, "Not saved. Check the highlighted boxes.")
    rows = [{"f": f, "qty": form[f"qty_{f.line.pk}"] if form else None,
             "cost": form[f"cost_{f.line.pk}"] if form else None,
             "remove": form[f"remove_{f.line.pk}"] if form else None} for f in figures.lines]
    return render(request, "restock/plan_detail.html", {
        "plan": plan, "figures": figures, "form": form, "rows": rows,
        "add_product": AddProductForm(plan=plan) if can_edit else None,
        "add_item": AddItemForm() if can_edit else None,
    })


@require_POST
@perm_required("restock.change_restockplan")
def line_add(request, pk: int):
    plan = get_object_or_404(RestockPlan, pk=pk)
    if request.POST.get("kind") == "product":
        form = AddProductForm(request.POST, plan=plan)
        kwargs = lambda d: {"product": d["product"]}  # noqa: E731
    else:
        form = AddItemForm(request.POST)
        kwargs = lambda d: {"description": d["description"], "unit": d.get("unit", ""),  # noqa: E731
                            "quantity": d["quantity"], "unit_cost": d.get("unit_cost")}
    if form.is_valid():
        try:
            line = services.add_line(plan, user=request.user, **kwargs(form.cleaned_data))
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            messages.success(request, f"Added {line.description}.")
    else:
        messages.error(request, "Not added: " + " ".join(e for errors in form.errors.values() for e in errors))
    return redirect("restock:detail", pk=plan.pk)


@require_http_methods(["GET", "POST"])
@perm_required("restock.add_restockpurchase")
def purchase_create(request, pk: int, line_pk: int):
    plan = get_object_or_404(RestockPlan, pk=pk)
    line = get_object_or_404(RestockLine.objects.select_related("product__stock_unit"), pk=line_pk, plan=plan)
    figures = next(f for f in services.plan_figures(plan).lines if f.line.pk == line.pk)
    initial = {}
    if figures.remaining:
        initial["quantity"] = figures.remaining.normalize()
        if line.planned_unit_cost is not None:
            initial["amount_paid"] = quantize(line.planned_unit_cost * figures.remaining)
    form = PurchaseForm(request.POST or None, unit_label=line.unit_label, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.record_purchase(line, user=request.user, quantity=d["quantity"], amount_paid=d["amount_paid"],
                                     purchase_date=d["purchase_date"], reference=d.get("reference", ""),
                                     submission_token=d["token"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            stocked = " and added to stock" if line.product_id and line.product.track_stock else ""
            messages.success(request, f"Recorded {line.description}{stocked}.")
            return redirect("restock:detail", pk=plan.pk)
    return render(request, "restock/purchase_form.html", {"plan": plan, "line": line, "figures": figures, "form": form})


@require_http_methods(["GET", "POST"])
@perm_required("restock.add_restockpurchase")
def purchase_reverse(request, pk: int, purchase_pk: int):
    purchase = get_object_or_404(RestockPurchase.objects.select_related("line__plan", "line__product"),
                                 pk=purchase_pk, line__plan_id=pk)
    form = ReverseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.reverse_purchase(purchase, user=request.user, reason=form.cleaned_data["reason"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Purchase reversed." + (" Its stock was taken back out." if purchase.stock_movement_id else ""))
            return redirect("restock:detail", pk=pk)
    return render(request, "restock/purchase_reverse.html", {"purchase": purchase, "form": form})
