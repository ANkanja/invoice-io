"""
The only code allowed to change stock. Every change:

1. runs in a database transaction,
2. locks the affected product rows (ordered by id to avoid deadlocks),
3. writes one append-only StockMovement, and
4. updates Product.stock_qty with an F() expression in the same transaction.

Invoice rules
-------------
* Stock leaves when an invoice is ISSUED (= the sale is finalised). Drafts never
  touch stock.
* Cancelling an issued invoice writes one SALE_REVERSAL per SALE movement, linked
  one-to-one to it. The reversal uses the quantities that were actually deducted,
  even if the product's settings changed since.
* A correction = cancel (stock returns) + issue a replacement (stock leaves again).
* Database constraints make a second deduction or second restoration for the same
  invoice line impossible, even if the code were called twice.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum

from core.models import BusinessSettings
from core.money import format_quantity, to_decimal
from products.models import Product

from .models import StockMovement

Kind = StockMovement.Kind


class StockError(ValidationError):
    pass


class InsufficientStockError(StockError):
    pass


def _positive(quantity) -> Decimal:
    qty = to_decimal(quantity)
    if qty <= 0:
        raise StockError("Quantity must be greater than zero.")
    if qty != qty.quantize(Decimal("0.01")):
        raise StockError("Quantity can have at most 2 decimal places.")
    return qty


def _lock_products(product_ids) -> dict[int, Product]:
    qs = Product.objects.select_for_update().filter(pk__in=list(product_ids)).order_by("pk")
    return {p.pk: p for p in qs}


def _lock_tracked(product: Product) -> Product:
    locked = _lock_products([product.pk])[product.pk]
    if not locked.track_stock:
        raise StockError(f"Stock tracking is switched off for {locked}.")
    return locked


def _apply(product: Product, quantity: Decimal, kind: str, user, **fields) -> StockMovement:
    Product.objects.filter(pk=product.pk).update(stock_qty=F("stock_qty") + quantity)
    product.refresh_from_db(fields=["stock_qty"])
    return StockMovement.objects.create(
        product=product, kind=kind, quantity=quantity, balance_after=product.stock_qty, created_by=user, **fields
    )


def _negative_allowed() -> bool:
    return BusinessSettings.load().allow_negative_stock


# --- Manual movements ---------------------------------------------------------

@transaction.atomic
def record_stock_in(product, quantity, user, *, unit_cost=None, reference="", notes="") -> StockMovement:
    qty = _positive(quantity)
    if unit_cost is not None and to_decimal(unit_cost) < 0:
        raise StockError("Unit cost cannot be negative.")
    locked = _lock_tracked(product)
    return _apply(locked, qty, Kind.STOCK_IN, user, unit_cost=unit_cost, reference=reference, notes=notes)


@transaction.atomic
def record_stock_out(product, quantity, user, *, reason, reference="", notes="") -> StockMovement:
    qty = _positive(quantity)
    if reason not in StockMovement.OutReason.values:
        raise StockError("Choose a reason for removing stock.")
    locked = _lock_tracked(product)
    if not _negative_allowed() and locked.stock_qty - qty < 0:
        raise InsufficientStockError(
            f"Only {format_quantity(locked.stock_qty)} {locked.unit} of {locked} in stock."
        )
    return _apply(locked, -qty, Kind.STOCK_OUT, user, out_reason=reason, reference=reference, notes=notes)


@transaction.atomic
def record_stock_count(product, counted_quantity, user, *, notes="") -> StockMovement | None:
    """Set stock to a physically counted figure. Writes the difference as an
    ADJUSTMENT; returns None when the count already matches."""
    counted = to_decimal(counted_quantity)
    if counted < 0:
        raise StockError("A counted quantity cannot be negative.")
    locked = _lock_tracked(product)
    difference = counted - locked.stock_qty
    if difference == 0:
        return None
    return _apply(locked, difference, Kind.ADJUSTMENT, user, notes=notes or "Stock count")


# --- Invoice-driven movements -----------------------------------------------------

@transaction.atomic
def deduct_stock_for_invoice(invoice, user) -> list[StockMovement]:
    """Called by invoices.services.issue_invoice inside its transaction."""
    lines = list(
        invoice.lines.filter(product__isnull=False, product__track_stock=True).select_related("product")
    )
    if not lines:
        return []
    if StockMovement.objects.filter(invoice_line__invoice=invoice, kind=Kind.SALE).exists():
        raise StockError(f"Stock for invoice {invoice.number} has already been deducted.")

    needed: dict[int, Decimal] = defaultdict(Decimal)
    for line in lines:
        needed[line.product_id] += line.quantity
    products = _lock_products(needed)

    if not _negative_allowed():
        shortages = [
            f"{products[pid]}: need {format_quantity(qty)}, have {format_quantity(products[pid].stock_qty)}"
            for pid, qty in needed.items()
            if products[pid].stock_qty < qty
        ]
        if shortages:
            raise InsufficientStockError("Not enough stock to issue this invoice — " + "; ".join(shortages))

    return [
        _apply(products[line.product_id], -line.quantity, Kind.SALE, user,
               invoice_line=line, reference=invoice.number or "")
        for line in lines
    ]


@transaction.atomic
def restore_stock_for_invoice(invoice, user) -> list[StockMovement]:
    """Called by invoices.services.cancel_invoice. Reverses exactly what was
    deducted, once."""
    sales = list(
        StockMovement.objects.filter(
            invoice_line__invoice=invoice, kind=Kind.SALE, reversed_by__isnull=True
        ).order_by("pk")
    )
    if not sales:
        return []
    products = _lock_products({s.product_id for s in sales})
    return [
        _apply(products[sale.product_id], -sale.quantity, Kind.SALE_REVERSAL, user,
               invoice_line_id=sale.invoice_line_id, reverses=sale,
               reference=invoice.number or "", notes="Invoice cancelled")
        for sale in sales
    ]


# --- Integrity check --------------------------------------------------------------

def find_stock_discrepancies() -> list[tuple[Product, Decimal, Decimal]]:
    """Products whose cached stock_qty differs from the ledger total."""
    ledger = dict(
        StockMovement.objects.values("product").annotate(total=Sum("quantity")).values_list("product", "total")
    )
    problems = []
    for product in Product.objects.all().order_by("pk"):
        expected = ledger.get(product.pk) or Decimal("0.00")
        if product.stock_qty != expected:
            problems.append((product, product.stock_qty, expected))
    return problems
