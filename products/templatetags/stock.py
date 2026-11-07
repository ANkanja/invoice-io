from django import template

from products.units import format_in_display_unit

register = template.Library()


@register.simple_tag
def stock_amount(quantity, product, signed=False):
    """A stock quantity (in the product's stock unit) in friendly units: '18.5 L', '+3 bottles'."""
    if quantity is None:
        return ""
    text = format_in_display_unit(abs(quantity) if signed else quantity, product.stock_unit)
    if signed:
        return f"+{text}" if quantity > 0 else f"−{text}"
    return text
