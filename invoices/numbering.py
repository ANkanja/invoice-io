"""
Invoice numbers are allocated on the server, inside the issuing transaction.

The counter is bumped with a single `UPDATE … SET last_number = last_number + 1`,
which the database serialises: on PostgreSQL the row is locked until the
transaction commits; on SQLite the whole database is write-locked. Either way two
concurrent requests cannot read the same value. If the issuing transaction rolls
back (e.g. not enough stock) the increment rolls back too, so no gap is left.
The `unique` constraint on Invoice.number is the final safety net.
"""

import datetime

from django.db import transaction
from django.db.models import F

from .models import Invoice, InvoiceSequence


def format_invoice_number(prefix: str, year: int, number: int, padding: int, yearly: bool) -> str:
    running = f"{number:0{padding}d}"
    return f"{prefix}-{year}-{running}" if yearly else f"{prefix}-{running}"


def allocate_invoice_number(*, prefix: str, padding: int, reset_yearly: bool, on_date: datetime.date) -> str:
    year = on_date.year if reset_yearly else 0
    with transaction.atomic():
        seq, _ = InvoiceSequence.objects.get_or_create(prefix=prefix, year=year)
        # Skip any number already taken (e.g. after a prefix change back and forth).
        for _attempt in range(1000):
            InvoiceSequence.objects.filter(pk=seq.pk).update(last_number=F("last_number") + 1)
            seq.refresh_from_db(fields=["last_number"])
            candidate = format_invoice_number(prefix, on_date.year, seq.last_number, padding, reset_yearly)
            if not Invoice.objects.filter(number=candidate).exists():
                return candidate
    raise RuntimeError("Could not allocate a free invoice number.")  # pragma: no cover
