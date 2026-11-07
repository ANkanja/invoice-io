"""
Money helpers. All financial arithmetic in the project goes through Decimal;
binary floats are rejected outright so they can never sneak into totals.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def to_decimal(value) -> Decimal:
    """Convert int/str/Decimal to Decimal. Floats are refused on purpose."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TypeError("Use Decimal or str for money, never float.")
    if value is None or value == "":
        return ZERO
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Not a valid amount: {value!r}") from exc


def quantize(value) -> Decimal:
    """Round to cents using ROUND_HALF_UP (KES 0.005 -> 0.01)."""
    return to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def format_money(value, currency: str = "KES") -> str:
    """'KES 1,234.50' — consistent everywhere (templates, PDFs, exports)."""
    amount = quantize(value)
    sign = "-" if amount < 0 else ""
    return f"{currency} {sign}{abs(amount):,.2f}".strip()


def format_quantity(value) -> str:
    """For display: drop trailing zeros and group thousands:
    2.00 -> '2', 2.50 -> '2.5', 1500 -> '1,500'."""
    d = to_decimal(value)
    text = f"{d:,f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
