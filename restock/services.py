"""
Restock planning. Views call these; they never touch stock or purchases directly.

Suggested quantity for a product (in its friendly unit, rounded up to a whole
unit):

    average sold per month over the last 3 months
  + its low-stock alert level (so you stay above it all month)
  - what's in stock now
  (never below zero)

Sales are the stock ledger's sale movements minus sale reversals, so cancelled
and amended invoices are counted correctly. The planned cost starts from the
last price you entered on a Stock in for that product.
"""

import datetime
import math
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from core.models import AuditLog
from core.money import quantize, to_decimal
from inventory.models import StockMovement
from inventory.services import record_stock_in, record_stock_out
from products.models import Product
from products.units import convert, conversion_factor, display_unit_for

from .models import RestockLine, RestockPlan, RestockPurchase

SALES_MONTHS = 3
QTY_PLACES = Decimal("0.001")


def month_start(day: datetime.date) -> datetime.date:
    return day.replace(day=1)


def next_month(day: datetime.date) -> datetime.date:
    first = month_start(day)
    return (first + datetime.timedelta(days=32)).replace(day=1)


def unit_label(product: Product) -> str:
    """What plan quantities are counted in for this product: 'L', 'kg', 'bottles'."""
    unit = display_unit_for(product.stock_unit)
    return unit.plural if unit.dimension == "count" else unit.symbol


def to_display(product: Product, stock_qty) -> Decimal:
    return convert(to_decimal(stock_qty), product.stock_unit, display_unit_for(product.stock_unit))


def to_stock(product: Product, display_qty) -> Decimal:
    return convert(to_decimal(display_qty), display_unit_for(product.stock_unit), product.stock_unit)


# --- Suggestions ------------------------------------------------------------------------------

def monthly_sales(product: Product, today: datetime.date, months: int = SALES_MONTHS) -> Decimal:
    """Average sold per month over the last `months` months, in stock units."""
    since = today - datetime.timedelta(days=30 * months)
    total = (StockMovement.objects
             .filter(product=product, kind__in=[StockMovement.Kind.SALE, StockMovement.Kind.SALE_REVERSAL],
                     created_at__date__gte=since, created_at__date__lte=today)
             .aggregate(t=Sum("quantity"))["t"] or Decimal("0"))
    return max(Decimal("0"), -total) / months


def last_unit_cost(product: Product) -> Decimal | None:
    """Last price paid, per friendly unit (from the latest Stock in with a cost)."""
    movement = (StockMovement.objects.filter(product=product, kind=StockMovement.Kind.STOCK_IN, unit_cost__isnull=False)
                .order_by("-created_at", "-pk").first())
    if movement is None or not movement.quantity:
        return None
    # unit_cost is per unit as typed; entered_quantity × unit_cost = what was paid.
    paid = movement.unit_cost * (movement.entered_quantity or movement.quantity)
    per_stock_unit = paid / movement.quantity
    return quantize(per_stock_unit * conversion_factor(display_unit_for(product.stock_unit), product.stock_unit))


@dataclass(frozen=True)
class Suggestion:
    monthly_sales: Decimal   # friendly unit
    stock: Decimal           # friendly unit
    quantity: Decimal        # friendly unit, whole units, ≥ 0


def suggest(product: Product, today: datetime.date) -> Suggestion:
    sales = monthly_sales(product, today)
    alert = product.low_stock_threshold or Decimal("0")
    need = sales + alert - product.stock_qty
    need_display = to_display(product, need) if need > 0 else Decimal("0")
    return Suggestion(
        monthly_sales=to_display(product, sales).quantize(QTY_PLACES),
        stock=to_display(product, product.stock_qty).quantize(QTY_PLACES),
        quantity=Decimal(math.ceil(need_display)),
    )


# --- Plans ------------------------------------------------------------------------------------

def _product_line(plan: RestockPlan, product: Product, position: int, today: datetime.date,
                  quantity=None, unit_cost=None) -> RestockLine:
    s = suggest(product, today)
    return RestockLine(
        plan=plan, position=position, product=product, description=product.name, unit_label=unit_label(product),
        suggested_qty=s.quantity, monthly_sales=s.monthly_sales, stock_at_planning=s.stock,
        planned_qty=s.quantity if quantity is None else to_decimal(quantity),
        planned_unit_cost=last_unit_cost(product) if unit_cost is None else quantize(unit_cost),
    )


@transaction.atomic
def create_plan(*, month: datetime.date, user, budget=None, today: datetime.date | None = None) -> RestockPlan:
    """A plan for the month: every stock-tracked product that needs restocking,
    with the suggested quantity and last cost, plus the other items (raw
    materials, transport…) from the latest earlier plan."""
    today = today or timezone.localdate()
    month = month_start(month)
    if RestockPlan.objects.filter(month=month).exists():
        raise ValidationError(f"There is already a plan for {month:%B %Y}.")
    if budget is not None and to_decimal(budget) < 0:
        raise ValidationError("The budget can't be negative.")
    plan = RestockPlan.objects.create(month=month, budget=None if budget is None else quantize(budget), created_by=user)
    lines = []
    for product in Product.objects.filter(is_active=True, track_stock=True).select_related("stock_unit").order_by("name"):
        line = _product_line(plan, product, len(lines) + 1, today)
        if line.suggested_qty > 0:
            lines.append(line)
    previous = RestockPlan.objects.filter(month__lt=month).order_by("-month").first()
    if previous:
        for old in previous.lines.filter(product__isnull=True):
            lines.append(RestockLine(plan=plan, position=len(lines) + 1, description=old.description,
                                     unit_label=old.unit_label, planned_qty=old.planned_qty,
                                     planned_unit_cost=old.planned_unit_cost))
    RestockLine.objects.bulk_create(lines)
    AuditLog.record(actor=user, action="restock.plan_created", obj=plan, changes={"lines": len(lines)})
    return plan


@transaction.atomic
def add_line(plan: RestockPlan, *, user, product: Product | None = None, description: str = "", unit: str = "",
             quantity=None, unit_cost=None, today: datetime.date | None = None) -> RestockLine:
    position = (plan.lines.aggregate(m=Max("position"))["m"] or 0) + 1
    if product is not None:
        if plan.lines.filter(product=product).exists():
            raise ValidationError(f"{product} is already in this plan.")
        line = _product_line(plan, product, position, today or timezone.localdate(), quantity, unit_cost)
    else:
        description = " ".join((description or "").split())
        if not description:
            raise ValidationError("Say what the item is.")
        line = RestockLine(plan=plan, position=position, description=description[:150],
                           unit_label=" ".join((unit or "").split())[:30],
                           planned_qty=to_decimal(quantity or 0),
                           planned_unit_cost=None if unit_cost in (None, "") else quantize(unit_cost))
    line.full_clean(exclude=["plan", "product"])
    line.save()
    AuditLog.record(actor=user, action="restock.line_added", obj=plan, changes={"item": line.description})
    return line


@transaction.atomic
def save_plan(plan: RestockPlan, *, user, budget, notes: str, lines: list[dict]) -> RestockPlan:
    """lines: {"line": RestockLine, "quantity", "unit_cost", "remove"}. Lines
    with purchases can't be removed (they're part of the record)."""
    plan.budget = None if budget in (None, "") else quantize(budget)
    plan.notes = (notes or "").strip()
    plan.save()
    for item in lines:
        line = item["line"]
        if item.get("remove"):
            if line.purchases.exists():
                raise ValidationError(f"“{line.description}” has purchases recorded, so it can't be removed.")
            line.delete()
            continue
        line.planned_qty = to_decimal(item.get("quantity") or 0)
        cost = item.get("unit_cost")
        line.planned_unit_cost = None if cost in (None, "") else quantize(cost)
        line.full_clean(exclude=["plan", "product"])
        line.save(update_fields=["planned_qty", "planned_unit_cost"])
    AuditLog.record(actor=user, action="restock.plan_saved", obj=plan)
    return plan


# --- Buying -----------------------------------------------------------------------------------

@transaction.atomic
def record_purchase(line: RestockLine, *, user, quantity, amount_paid, purchase_date: datetime.date,
                    reference: str = "", submission_token=None) -> RestockPurchase:
    """What was really bought for a line. For a stock-tracked product it is
    also recorded as Stock in (same quantity, cost per unit = amount ÷ quantity)."""
    quantity, amount_paid = to_decimal(quantity), quantize(amount_paid)
    if quantity <= 0:
        raise ValidationError("Enter how much you bought.")
    if quantity != quantity.quantize(QTY_PLACES):
        raise ValidationError("Use at most 3 decimal places for the quantity.")
    if amount_paid < 0:
        raise ValidationError("The amount paid can't be negative.")
    if purchase_date > timezone.localdate():
        raise ValidationError("The purchase date can't be in the future.")
    if submission_token and RestockPurchase.objects.filter(submission_token=submission_token).exists():
        raise ValidationError("This purchase was already saved.")
    reference = (reference or "").strip()[:80]
    movement = None
    product = line.product
    if product is not None and product.track_stock:
        stock_qty = to_stock(product, quantity)
        if stock_qty != stock_qty.quantize(QTY_PLACES):
            raise ValidationError(f"That's more precise than the stock is counted in ({product.stock_unit.symbol}).")
        movement = record_stock_in(
            product, stock_qty, user, unit_cost=quantize(amount_paid / quantity),
            reference=reference or f"Restock {line.plan.month:%b %Y}",
            notes=f"Bought for the {line.plan.month:%B %Y} restock plan",
            entered=(quantity, line.unit_label),
        )
    purchase = RestockPurchase.objects.create(
        line=line, quantity=quantity, amount_paid=amount_paid, purchase_date=purchase_date, reference=reference,
        stock_movement=movement, submission_token=submission_token, created_by=user)
    AuditLog.record(actor=user, action="restock.purchase_recorded", obj=line.plan,
                    changes={"item": line.description, "quantity": str(quantity), "paid": str(amount_paid)})
    return purchase


@transaction.atomic
def reverse_purchase(purchase: RestockPurchase, *, user, reason: str) -> RestockPurchase:
    """Undo a purchase entered by mistake: kept on record, no longer counted,
    and any stock it added is taken back out."""
    purchase = RestockPurchase.objects.select_for_update().get(pk=purchase.pk)
    if purchase.is_reversed:
        raise ValidationError("This purchase was already reversed.")
    reason = " ".join((reason or "").split())
    if not reason:
        raise ValidationError("Say why you're reversing it.")
    if purchase.stock_movement_id:
        movement = purchase.stock_movement
        purchase.reversal_movement = record_stock_out(
            movement.product, movement.quantity, user, reason=StockMovement.OutReason.OTHER,
            reference=movement.reference, notes=f"Restock purchase reversed: {reason}"[:500])
    purchase.is_reversed = True
    purchase.reversed_at = timezone.now()
    purchase.reversed_by = user
    purchase.reversal_reason = reason[:200]
    purchase.save(update_fields=["is_reversed", "reversed_at", "reversed_by_id", "reversal_reason",
                                 "reversal_movement_id"])
    AuditLog.record(actor=user, action="restock.purchase_reversed", obj=purchase.line.plan, reason=reason,
                    changes={"item": purchase.line.description})
    return purchase


# --- Figures ----------------------------------------------------------------------------------

@dataclass
class LineFigures:
    line: RestockLine
    bought: Decimal
    paid: Decimal
    purchases: list

    @property
    def remaining(self) -> Decimal:
        return max(Decimal("0"), self.line.planned_qty - self.bought)

    @property
    def status(self) -> str:
        if not self.bought:
            return "not_bought"
        if self.bought < self.line.planned_qty:
            return "partly"
        if self.bought == self.line.planned_qty:
            return "done"
        return "more"

    @property
    def cost_saving(self) -> Decimal:
        return -(self.cost_difference or Decimal("0"))

    @property
    def cost_difference(self) -> Decimal | None:
        """Paid minus what the bought quantity was planned to cost (positive = dearer)."""
        if not self.bought or self.line.planned_unit_cost is None:
            return None
        return self.paid - quantize(self.bought * self.line.planned_unit_cost)


@dataclass
class PlanFigures:
    plan: RestockPlan
    lines: list[LineFigures]

    @property
    def planned_total(self) -> Decimal:
        return sum((f.line.planned_total for f in self.lines), Decimal("0.00"))

    @property
    def spent(self) -> Decimal:
        return sum((f.paid for f in self.lines), Decimal("0.00"))

    @property
    def budget_left(self) -> Decimal | None:
        return None if self.plan.budget is None else self.plan.budget - self.spent

    @property
    def over_budget_by(self) -> Decimal:
        """How far the plan itself goes over the budget (0 if it fits)."""
        if self.plan.budget is None:
            return Decimal("0.00")
        return max(Decimal("0.00"), self.planned_total - self.plan.budget)

    @property
    def lines_done(self) -> int:
        return sum(1 for f in self.lines if f.status in ("done", "more"))


def plan_figures(plan: RestockPlan) -> PlanFigures:
    lines = list(plan.lines.select_related("product__stock_unit").prefetch_related("purchases__created_by"))
    figures = []
    for line in lines:
        counted = [p for p in line.purchases.all() if not p.is_reversed]
        figures.append(LineFigures(line=line, bought=sum((p.quantity for p in counted), Decimal("0")),
                                   paid=sum((p.amount_paid for p in counted), Decimal("0.00")),
                                   purchases=list(line.purchases.all())))
    return PlanFigures(plan=plan, lines=figures)
