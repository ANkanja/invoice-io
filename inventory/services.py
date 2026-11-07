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
from core.money import to_decimal
from products.models import Product
from products.units import format_in_display_unit

from .models import StockMovement

Kind = StockMovement.Kind


def _amount(product: Product, qty) -> str:
    """Stock-unit quantity in friendly units: 18,500 ml → "18.5 L"."""
    return format_in_display_unit(qty, product.stock_unit)


class StockError(ValidationError):
    pass


class InsufficientStockError(StockError):
    pass


class DuplicateSubmission(StockError):
    """The same form was submitted twice (double tap, retry). Nothing new was recorded."""


def _check_token(token) -> None:
    if token and StockMovement.objects.filter(submission_token=token).exists():
        raise DuplicateSubmission("This entry was already recorded.")


def _positive(quantity) -> Decimal:
    qty = to_decimal(quantity)
    if qty <= 0:
        raise StockError("Quantity must be greater than zero.")
    if qty != qty.quantize(Decimal("0.001")):
        raise StockError("That quantity is too precise; use fewer decimal places.")
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


def _entered(entered) -> dict:
    if not entered:
        return {}
    qty, label = entered
    return {"entered_quantity": to_decimal(qty), "entered_unit": label[:60]}


def _negative_allowed() -> bool:
    return BusinessSettings.load().allow_negative_stock


# --- Manual movements ---------------------------------------------------------

@transaction.atomic
def record_stock_in(product, quantity, user, *, unit_cost=None, reference="", notes="",
                    submission_token=None, entered=None) -> StockMovement:
    """quantity is in the product's stock unit; `entered` = (qty, label) as typed."""
    _check_token(submission_token)
    qty = _positive(quantity)
    if unit_cost is not None and to_decimal(unit_cost) < 0:
        raise StockError("Unit cost cannot be negative.")
    locked = _lock_tracked(product)
    return _apply(locked, qty, Kind.STOCK_IN, user, unit_cost=unit_cost, reference=reference, notes=notes,
                  submission_token=submission_token, **_entered(entered))


@transaction.atomic
def record_stock_out(product, quantity, user, *, reason, reference="", notes="",
                     submission_token=None, entered=None) -> StockMovement:
    _check_token(submission_token)
    qty = _positive(quantity)
    if reason not in StockMovement.OutReason.values:
        raise StockError("Choose a reason for removing stock.")
    locked = _lock_tracked(product)
    if not _negative_allowed() and locked.stock_qty - qty < 0:
        raise InsufficientStockError(
            f"Only {_amount(locked, locked.stock_qty)} of {locked} in stock, so {_amount(locked, qty)} can't be removed."
        )
    return _apply(locked, -qty, Kind.STOCK_OUT, user, out_reason=reason, reference=reference, notes=notes,
                  submission_token=submission_token, **_entered(entered))


@transaction.atomic
def record_stock_count(product, counted_quantity, user, *, notes="", submission_token=None,
                       entered=None) -> StockMovement | None:
    """Set stock to a physically counted figure. Writes the difference as an
    ADJUSTMENT; returns None when the count already matches."""
    _check_token(submission_token)
    counted = to_decimal(counted_quantity)
    if counted != counted.quantize(Decimal("0.001")):
        raise StockError("That quantity is too precise; use fewer decimal places.")
    if counted < 0:
        raise StockError("A counted quantity cannot be negative.")
    locked = _lock_tracked(product)
    difference = counted - locked.stock_qty
    if difference == 0:
        return None
    return _apply(locked, difference, Kind.ADJUSTMENT, user, notes=notes or "Stock count",
                  submission_token=submission_token, **_entered(entered))


@transaction.atomic
def record_return(product, quantity, user, *, reference="", notes="", submission_token=None,
                  entered=None) -> StockMovement:
    """A customer brought goods back into stock (part of an order). For a whole
    invoice, cancel or amend the invoice instead; that reverses its stock."""
    _check_token(submission_token)
    qty = _positive(quantity)
    locked = _lock_tracked(product)
    return _apply(locked, qty, Kind.RETURN, user, reference=reference, notes=notes,
                  submission_token=submission_token, **_entered(entered))


# --- Invoice-driven movements -----------------------------------------------------

@transaction.atomic
def deduct_stock_for_invoice(invoice, user) -> list[StockMovement]:
    """Called when an invoice is issued (or amended), inside that transaction.
    Deducts each current line's stock quantity (already converted to the
    product's stock unit, e.g. 3 × 500 ml bottles → 1,500 ml)."""
    lines = list(
        invoice.lines.filter(product__isnull=False, product__track_stock=True, base_quantity__isnull=False)
        .select_related("product")
    )
    if not lines:
        return []
    if StockMovement.objects.filter(invoice_line__in=lines, kind=Kind.SALE).exists():
        raise StockError(f"Stock for invoice {invoice.number} has already been deducted.")

    needed: dict[int, Decimal] = defaultdict(Decimal)
    for line in lines:
        needed[line.product_id] += line.base_quantity
    products = _lock_products(needed)

    if not _negative_allowed():
        shortages = [
            f"{products[pid]}: need {_amount(products[pid], qty)}, have {_amount(products[pid], products[pid].stock_qty)}"
            for pid, qty in needed.items()
            if products[pid].stock_qty < qty
        ]
        if shortages:
            raise InsufficientStockError("Not enough stock to issue this invoice — " + "; ".join(shortages))

    return [
        _apply(products[line.product_id], -line.base_quantity, Kind.SALE, user,
               invoice_line=line, reference=invoice.number or "",
               entered_quantity=line.quantity,
               entered_unit=(line.unit if line.quantity == 1 else (line.unit_plural or line.unit))[:60])
        for line in lines
    ]


@transaction.atomic
def restore_stock_for_invoice(invoice, user, note: str = "Invoice cancelled") -> list[StockMovement]:
    """Called when an invoice is cancelled or amended. Reverses exactly what was
    deducted and not yet reversed, once."""
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
               reference=invoice.number or "", notes=note)
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
        expected = ledger.get(product.pk) or Decimal("0")
        if product.stock_qty != expected:
            problems.append((product, product.stock_qty, expected))
    return problems
