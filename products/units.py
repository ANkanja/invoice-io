"""
Unit conversions.

Every unit belongs to a dimension:

* volume: ml (factor 1) and L (factor 1,000), so 1 L = 1,000 ml
* mass: g (factor 1) and kg (factor 1,000), so 1 kg = 1,000 g
* count: piece, bottle, container, carton, pack, … Each count unit converts
  only to itself. "1 carton = 12 bottles" is product packaging, configured on
  the product's selling options, never a global rule.

Conversions across dimensions (litres to kilograms) are always refused,
because they depend on the product's density.

Volume and mass units are fixed (seeded by migration) with power-of-ten
factors, so every conversion is exact in Decimal. Only count units can be
added later.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from core.money import format_quantity, to_decimal


class IncompatibleUnits(ValidationError):
    pass


def check_compatible(from_unit, to_unit) -> None:
    if from_unit.dimension != to_unit.dimension:
        raise IncompatibleUnits(
            f"{from_unit.symbol} ({from_unit.get_dimension_display().lower()}) can't be converted to "
            f"{to_unit.symbol} ({to_unit.get_dimension_display().lower()})."
        )
    if from_unit.dimension == "count" and from_unit.pk != to_unit.pk:
        raise IncompatibleUnits(
            f"{from_unit.name} and {to_unit.name} are different packaging; set up the conversion on the product."
        )


def conversion_factor(from_unit, to_unit) -> Decimal:
    """Multiply a quantity in `from_unit` by this to get it in `to_unit`."""
    check_compatible(from_unit, to_unit)
    return (Decimal(from_unit.factor) / Decimal(to_unit.factor)).normalize()


def convert(quantity, from_unit, to_unit) -> Decimal:
    return to_decimal(quantity) * conversion_factor(from_unit, to_unit)


def compatible_units(unit, queryset=None):
    """Units a quantity in `unit` can be entered in (same dimension; count → itself only)."""
    from .models import Unit

    qs = queryset if queryset is not None else Unit.objects.filter(is_active=True)
    if unit.dimension == "count":
        return qs.filter(pk=unit.pk)
    return qs.filter(dimension=unit.dimension).order_by("factor")


_display_cache: dict = {}


def clear_unit_cache(**kwargs) -> None:
    _display_cache.clear()


def display_unit_for(stock_unit):
    """Friendly unit for showing stock: litres for ml, kilograms for g.
    Cached (units rarely change; the cache is cleared whenever one is saved)."""
    from .models import Unit

    if stock_unit.dimension == "count":
        return stock_unit
    key = (stock_unit.dimension, stock_unit.factor)
    if key not in _display_cache:
        larger = Unit.objects.filter(dimension=stock_unit.dimension, factor__gt=stock_unit.factor,
                                     is_active=True).order_by("factor").first()
        _display_cache[key] = larger or stock_unit
    return _display_cache[key]


def format_amount(quantity, unit) -> str:
    """'18.5 L', '3 bottles', '1 bottle'."""
    if quantity is None:
        return ""
    text = format_quantity(quantity)
    if unit.dimension == "count":
        label = unit.name if to_decimal(quantity) == 1 else unit.plural
        return f"{text} {label}"
    return f"{text} {unit.symbol}"


def format_in_display_unit(quantity, stock_unit) -> str:
    """Stock quantity (in stock units) shown in the friendly unit."""
    if quantity is None:
        return ""
    display = display_unit_for(stock_unit)
    value = convert(quantity, stock_unit, display)
    # Thousands separators for readability; trailing zeros dropped.
    text = f"{value.normalize():,f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if display.dimension == "count":
        return f"{text} {display.name if value == 1 else display.plural}"
    return f"{text} {display.symbol}"
