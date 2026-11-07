"""
Pure invoice arithmetic — no database access, so it is easy to test and is the
single source of truth for every total the app stores or prints.

Rules
-----
* Money is Decimal, rounded to cents with ROUND_HALF_UP at each line.
* line gross   = quantity in pricing units × unit price
                 (e.g. 1,500 ml at KES 250 per L → 1.5 × 250; see products.pricing)
* line total   = line gross − line discount (discount may not exceed gross)
* subtotal     = Σ line totals
* after disc.  = subtotal − invoice discount (may not exceed subtotal)
* tax, exclusive prices:  tax = after disc. × rate/100;           total = after disc. + tax
* tax, inclusive prices:  tax = after disc. × rate/(100 + rate);  total = after disc.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Protocol

from django.core.exceptions import ValidationError

from core.money import ZERO, quantize, to_decimal

HUNDRED = Decimal("100")


class LineLike(Protocol):
    quantity: Decimal
    unit_price: Decimal
    line_discount: Decimal


@dataclass(frozen=True)
class LineInput:
    quantity: Decimal
    unit_price: Decimal
    line_discount: Decimal = ZERO


@dataclass(frozen=True)
class LineResult:
    gross: Decimal
    discount: Decimal
    total: Decimal


@dataclass(frozen=True)
class InvoiceTotals:
    lines: tuple[LineResult, ...]
    subtotal: Decimal
    line_discount_total: Decimal
    invoice_discount: Decimal
    taxable_amount: Decimal
    tax_total: Decimal
    total: Decimal


def calculate_line(quantity, unit_price, line_discount=ZERO) -> LineResult:
    qty = to_decimal(quantity)
    price = to_decimal(unit_price)
    discount = quantize(line_discount)
    if qty <= 0:
        raise ValidationError("Quantity must be greater than zero.", code="quantity")
    if price < 0:
        raise ValidationError("Unit price cannot be negative.", code="unit_price")
    if discount < 0:
        raise ValidationError("Discount cannot be negative.", code="line_discount")
    gross = quantize(qty * price)
    if discount > gross:
        raise ValidationError("A line discount cannot be more than the line amount.", code="line_discount")
    return LineResult(gross=gross, discount=discount, total=gross - discount)


def calculate_invoice(
    lines: Iterable[LineLike],
    invoice_discount=ZERO,
    tax_rate=ZERO,
    prices_include_tax: bool = False,
) -> InvoiceTotals:
    # Lines priced by measure carry price_quantity (e.g. 1,500 ml entered → 1.5 L);
    # simple lines and LineInput use quantity directly.
    results = tuple(
        calculate_line(getattr(l, "price_quantity", None) or l.quantity, l.unit_price, l.line_discount) for l in lines
    )
    subtotal = quantize(sum((r.total for r in results), ZERO))
    line_discounts = quantize(sum((r.discount for r in results), ZERO))

    discount = quantize(invoice_discount)
    if discount < 0:
        raise ValidationError("Invoice discount cannot be negative.", code="invoice_discount")
    if discount > subtotal:
        raise ValidationError("Invoice discount cannot be more than the subtotal.", code="invoice_discount")

    rate = to_decimal(tax_rate)
    if rate < 0 or rate > HUNDRED:
        raise ValidationError("Tax rate must be between 0 and 100.", code="tax_rate")

    after_discount = subtotal - discount
    if rate == 0:
        tax = ZERO
        total = after_discount
    elif prices_include_tax:
        total = after_discount
        tax = quantize(after_discount * rate / (HUNDRED + rate))
    else:
        tax = quantize(after_discount * rate / HUNDRED)
        total = after_discount + tax

    return InvoiceTotals(
        lines=results,
        subtotal=subtotal,
        line_discount_total=line_discounts,
        invoice_discount=discount,
        taxable_amount=quantize(total - tax),
        tax_total=quantize(tax),
        total=quantize(total),
    )
