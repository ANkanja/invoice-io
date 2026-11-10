"""
The message and links for sending an issued invoice to a customer by WhatsApp
or email.

The app has no email service of its own. The browser does the sending: on
phones that can share files it opens the share menu with the invoice image
attached; elsewhere it downloads the image and opens WhatsApp or the email app
with the customer and this message filled in (see static/ts/share.ts).
"""

import re

from django.urls import reverse

from core.money import format_money

from .models import Invoice
from .pdf import invoice_filename


def whatsapp_number(phone: str) -> str:
    """The number as WhatsApp links expect it: country code, digits only.
    Numbers without a country code are taken to be Kenyan (0712 345 678 →
    254712345678). Blank when it can't be read, so WhatsApp lets you pick the chat."""
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("254") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return "254" + digits[1:]
    if len(digits) == 9 and digits[0] in "17":
        return "254" + digits
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return digits  # another country, written with its code
    return ""


def _date(value) -> str:
    return f"{value.day} {value:%b %Y}"


def share_message(invoice: Invoice, seller: dict, balance) -> str:
    money = lambda value: format_money(value, invoice.currency)  # noqa: E731
    lines = [f"Hello {invoice.customer_name},", "",
             f"Please find invoice {invoice.number} dated {_date(invoice.issue_date)} for {money(invoice.total)}."]
    due = f" by {_date(invoice.due_date)}" if invoice.due_date else ""
    if balance <= 0:
        lines.append("It is fully paid. Thank you.")
    elif balance < invoice.total:
        lines.append(f"Balance due: {money(balance)}{due}.")
    elif due:
        lines.append(f"Kindly pay{due}.")
    lines += ["", "Thank you."]
    if seller.get("name"):
        lines.append(seller["name"])
    return "\n".join(lines)


def share_data(invoice: Invoice, seller: dict, balance) -> dict:
    """What the share buttons need (passed to the page as JSON)."""
    business = seller.get("name") or ""
    return {
        "imageUrl": reverse("invoices:image", args=[invoice.pk]),
        "filename": invoice_filename(invoice),
        "subject": f"Invoice {invoice.number}" + (f" from {business}" if business else ""),
        "text": share_message(invoice, seller, balance),
        "whatsapp": whatsapp_number(invoice.customer_phone),
        "email": invoice.customer_email,
    }
