from django import template

from core.money import format_money, format_quantity

register = template.Library()


@register.filter
def money(value, currency="KES"):
    """{{ invoice.total|money }} → 'KES 1,250.00'"""
    if value is None or value == "":
        return ""
    return format_money(value, currency)


@register.filter
def qty(value):
    """{{ product.stock_qty|qty }} → '12' or '2.5'"""
    if value is None or value == "":
        return ""
    return format_quantity(value)
