"""
Turns "quantity + unit, sold via this selling option" into everything an
invoice line needs: the quantity in pricing units (for the amount) and in
stock units (for deduction), plus readable labels. Pure arithmetic; no saving.

Examples (stock unit ml):
* "500 ml bottle" pack, KES 140, 5 entered           → 5 bottles; price qty 5;     stock 2,500 ml; KES 700
* "per litre" measure, KES 250, 1,500 entered in ml  → factor 0.001; price qty 1.5; stock 1,500 ml; KES 375
* "per litre" measure, KES 250, 2.5 entered in L     → factor 1;     price qty 2.5; stock 2,500 ml; KES 625

Packs are never pro-rated: a 500 ml bottle costs its own pack price.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError

from core.money import to_decimal

from .models import SellingOption, Unit
from .units import IncompatibleUnits, conversion_factor

STOCK_PLACES = Decimal("0.001")
PRICE_QTY_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class PricedQuantity:
    entered_quantity: Decimal
    unit: str                 # entered unit, singular ("ml", "bottle")
    unit_plural: str          # "ml", "bottles"
    pricing_method: str       # "package" / "measure"
    price_unit: str           # the price is per this ("L", "bottle")
    conversion_factor: Decimal  # entered unit → price unit
    price_quantity: Decimal   # quantity in price units (exact)
    base_quantity: Decimal    # quantity in the product's stock unit
    base_unit: str            # "ml", "g", "piece"


def _exact(value: Decimal, places: Decimal, what: str) -> Decimal:
    rounded = value.quantize(places)
    if rounded != value:
        raise ValidationError(f"That {what} is too precise; use fewer decimal places.")
    return rounded


def resolve_quantity(option: SellingOption, quantity, entered_unit: Unit | None = None) -> PricedQuantity:
    qty = to_decimal(quantity)
    if qty <= 0:
        raise ValidationError("Quantity must be greater than zero.")
    stock_unit = option.product.stock_unit

    if option.method == SellingOption.Method.PACKAGE:
        pack = option.package_unit
        if entered_unit is not None and entered_unit.pk != pack.pk:
            raise IncompatibleUnits(f"“{option.label}” is sold by the {pack.name}; enter the number of {pack.plural}.")
        base = qty * option.content_quantity
        return PricedQuantity(
            entered_quantity=qty, unit=pack.name, unit_plural=pack.plural, pricing_method=option.method,
            price_unit=pack.name, conversion_factor=Decimal("1"), price_quantity=qty,
            base_quantity=_exact(base, STOCK_PLACES, "quantity"), base_unit=stock_unit.symbol,
        )

    unit = entered_unit or option.price_unit
    factor = conversion_factor(unit, option.price_unit)          # raises IncompatibleUnits
    to_stock = conversion_factor(unit, stock_unit)
    return PricedQuantity(
        entered_quantity=qty, unit=unit.symbol, unit_plural=unit.symbol, pricing_method=option.method,
        price_unit=option.price_unit.symbol, conversion_factor=factor,
        price_quantity=_exact(qty * factor, PRICE_QTY_PLACES, "quantity"),
        base_quantity=_exact(qty * to_stock, STOCK_PLACES, "quantity"), base_unit=stock_unit.symbol,
    )


def units_for_option(option: SellingOption):
    """Units the quantity can be typed in for this option."""
    from .units import compatible_units

    if option.method == SellingOption.Method.PACKAGE:
        return [option.package_unit]
    return list(compatible_units(option.price_unit))


def stock_entry_choices(product) -> list[tuple[str, str, Decimal]]:
    """Ways to enter a stock quantity for this product on stock forms:
    any compatible unit ('unit:ml', 'unit:l') or one of its packs
    ('option:12' → 5 L container). Returns (value, label, factor to stock units)."""
    from .units import compatible_units, conversion_factor as factor_of, format_in_display_unit

    choices = []
    for unit in compatible_units(product.stock_unit):
        label = unit.plural if unit.dimension == "count" else f"{unit.plural} ({unit.symbol})"
        choices.append((f"unit:{unit.code}", label, factor_of(unit, product.stock_unit)))
    for option in product.options.filter(is_active=True, method=SellingOption.Method.PACKAGE).select_related("package_unit"):
        if product.stock_unit.dimension == "count" and option.content_quantity == 1 and option.package_unit_id == product.stock_unit_id:
            continue  # same as the plain unit above
        choices.append((f"option:{option.pk}",
                        f"{option.label} ({format_in_display_unit(option.content_quantity, product.stock_unit)} each)",
                        option.content_quantity))
    return choices


def to_stock_units(product, quantity, choice: str) -> tuple[Decimal, str]:
    """Convert a stock-form entry to the product's stock unit. Returns
    (quantity in stock units, readable label of what was entered)."""
    qty = to_decimal(quantity)
    for value, label, factor in stock_entry_choices(product):
        if value == choice:
            name = label.split(" (")[0]
            return _exact(qty * factor, STOCK_PLACES, "quantity"), name
    raise IncompatibleUnits("Choose a unit that matches how this product's stock is counted.")
