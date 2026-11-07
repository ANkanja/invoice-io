"""
Recording, applying and reversing payments. Views never write payments or
allocations themselves.

Rules
-----
* A payment is money actually received. It can be split across several
  issued invoices (allocations). Each allocation is at most that invoice's
  balance, and together they're at most the payment amount. Anything left over
  is **credit**: it stays on the payment and can be applied later.
* Payments are never edited or deleted. A mistake is **reversed** with a reason:
  its allocations stop counting, so the invoices it paid become unpaid again.
* Cancelling an invoice **releases** its allocations: the money goes back to the
  payments as credit. Issuing that invoice's replacement applies the released
  money to it automatically.
* The same M-Pesa or bank reference can't be recorded twice for one method.
* Every step runs in one database transaction with the invoice rows locked, and
  writes an audit entry.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import AuditLog
from core.money import format_money, quantize, to_decimal

from .models import Payment, PaymentAllocation, PaymentMethod

ZERO = Decimal("0.00")


class PaymentError(ValidationError):
    pass


class DuplicateSubmission(PaymentError):
    """The same form was sent twice (double tap, resubmit). Nothing new was recorded."""


@dataclass(frozen=True)
class Allocation:
    invoice: object
    amount: Decimal


def _invoice_model():
    from invoices.models import Invoice

    return Invoice


def _money(value) -> str:
    return format_money(value, "KES")


def suggest_allocations(invoices, amount) -> list[Allocation]:
    """Spread `amount` over invoices oldest first, never more than each balance
    (the default the payment form offers). Invoices need `balance` annotated."""
    left = quantize(amount)
    result = []
    for invoice in sorted(invoices, key=lambda i: (i.due_date or i.issue_date, i.issue_date, i.pk)):
        if left <= 0:
            break
        take = min(left, invoice.balance)
        if take > 0:
            result.append(Allocation(invoice, take))
            left -= take
    return result


def _apply(payment: Payment, allocations, user) -> list[PaymentAllocation]:
    """Create allocations for a (locked) payment. Validates every rule first."""
    Invoice = _invoice_model()
    wanted = [(a.invoice, quantize(a.amount)) for a in allocations if a.amount and to_decimal(a.amount) != 0]
    if not wanted:
        return []
    ids = [inv.pk for inv, _ in wanted]
    if len(set(ids)) != len(ids):
        raise PaymentError("The same invoice appears twice.")
    locked = {
        inv.pk: inv
        for inv in Invoice.objects.select_for_update().filter(pk__in=ids).order_by("pk")
    }
    balances = {
        inv.pk: inv.balance for inv in Invoice.objects.filter(pk__in=ids).with_payment_totals()
    }
    available = payment.get_unallocated()
    total = sum((amount for _, amount in wanted), ZERO)
    if total > available:
        raise PaymentError(
            f"That applies {_money(total)}, but only {_money(available)} of this payment is free to apply."
        )
    created = []
    for inv, amount in wanted:
        invoice = locked[inv.pk]
        if amount < 0:
            raise PaymentError("Amounts can't be negative.")
        if invoice.status != Invoice.Status.ISSUED:
            raise PaymentError(f"Payments can only go to issued invoices ({invoice.number or 'draft'} isn't).")
        if payment.customer_id and invoice.customer_id and invoice.customer_id != payment.customer_id:
            raise PaymentError(f"Invoice {invoice.number} belongs to a different customer.")
        if amount > balances[invoice.pk]:
            raise PaymentError(
                f"{_money(amount)} is more than the {_money(balances[invoice.pk])} still due on {invoice.number}."
            )
        if PaymentAllocation.objects.filter(payment=payment, invoice=invoice, released_at__isnull=True).exists():
            raise PaymentError(f"This payment is already applied to {invoice.number}.")
        created.append(PaymentAllocation.objects.create(payment=payment, invoice=invoice, amount=amount,
                                                        created_by=user))
    return created


@transaction.atomic
def record_payment(*, user, amount, method: PaymentMethod, payment_date=None, customer=None, payer_name="",
                   reference="", notes="", allocations=(), submission_token=None) -> Payment:
    """Record money received and apply it to invoices. Any amount not applied is
    kept as credit on the payment."""
    amount = quantize(amount)
    if amount <= 0:
        raise PaymentError("The amount must be more than zero.")
    payment_date = payment_date or timezone.localdate()
    if payment_date > timezone.localdate():
        raise PaymentError("The payment date can't be in the future.")
    if not method.is_active:
        raise PaymentError(f"{method} is switched off as a payment method.")
    reference = (reference or "").strip().upper()
    if method.requires_reference and not reference:
        raise PaymentError(f"Enter the {method} reference (e.g. the transaction code).")
    payer_name = " ".join((payer_name or (customer.name if customer else "")).split())
    if not payer_name:
        raise PaymentError("Enter who paid.")
    token = submission_token or uuid.uuid4()
    if Payment.objects.filter(submission_token=token).exists():
        raise DuplicateSubmission("This payment was already recorded.")
    if reference and Payment.objects.filter(method=method, reference=reference, is_reversed=False).exists():
        raise PaymentError(f"A {method} payment with reference {reference} is already recorded.")

    try:
        with transaction.atomic():
            payment = Payment.objects.create(
                customer=customer, payer_name=payer_name, amount=amount, payment_date=payment_date, method=method,
                reference=reference, notes=(notes or "").strip(), submission_token=token, created_by=user,
            )
    except IntegrityError as exc:  # raced with an identical submission
        if Payment.objects.filter(submission_token=token).exists():
            raise DuplicateSubmission("This payment was already recorded.") from exc
        raise PaymentError(f"A {method} payment with reference {reference} is already recorded.") from exc

    applied = _apply(payment, allocations, user)
    AuditLog.record(actor=user, action="payment.recorded", obj=payment, changes={
        "amount": str(amount), "method": method.name, "reference": reference,
        "applied": {a.invoice.number: str(a.amount) for a in applied},
    })
    return payment


@transaction.atomic
def apply_credit(payment: Payment, allocations, user) -> list[PaymentAllocation]:
    """Apply a payment's unused credit to invoices."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.is_reversed:
        raise PaymentError("This payment has been reversed.")
    applied = _apply(payment, allocations, user)
    if not applied:
        raise PaymentError("Enter an amount for at least one invoice.")
    AuditLog.record(actor=user, action="payment.applied", obj=payment,
                    changes={a.invoice.number: str(a.amount) for a in applied})
    return applied


@transaction.atomic
def reverse_payment(payment: Payment, user, reason: str) -> Payment:
    """Undo a payment recorded by mistake (or refunded). It stays on record but
    no longer counts towards any invoice."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    reason = (reason or "").strip()
    if not reason:
        raise PaymentError("Give a reason for reversing this payment.")
    if payment.is_reversed:
        raise PaymentError("This payment is already reversed.")
    invoices = list(payment.allocations.filter(released_at__isnull=True).values_list("invoice__number", flat=True))
    payment.is_reversed = True
    payment.reversed_at = timezone.now()
    payment.reversed_by = user
    payment.reversal_reason = reason
    payment.save()
    AuditLog.record(actor=user, action="payment.reversed", obj=payment, reason=reason,
                    changes={"amount": str(payment.amount), "invoices": invoices})
    return payment


def release_invoice_payments(invoice, user, reason: str) -> Decimal:
    """Called inside cancel_invoice: the invoice's payments become credit again.
    Returns the amount released."""
    released = ZERO
    for allocation in (PaymentAllocation.objects.select_for_update()
                       .filter(invoice=invoice, released_at__isnull=True).select_related("payment")):
        allocation.released_at = timezone.now()
        allocation.released_by = user
        allocation.release_reason = reason[:255]
        allocation.save()
        if not allocation.payment.is_reversed:
            released += allocation.amount
    return released


def apply_released_to_replacement(invoice, user) -> Decimal:
    """Called inside issue_invoice for a replacement: move the money released
    from the cancelled original onto the new invoice (up to its total)."""
    original = invoice.replaces
    if original is None:
        return ZERO
    payment_ids = (PaymentAllocation.objects.filter(invoice=original, released_at__isnull=False)
                   .values_list("payment_id", flat=True).distinct())
    applied = ZERO
    room = invoice.total - invoice.get_amount_paid()
    for payment in Payment.objects.select_for_update().filter(pk__in=list(payment_ids), is_reversed=False).order_by("pk"):
        if room <= 0:
            break
        take = min(payment.get_unallocated(), room)
        if take <= 0:
            continue
        PaymentAllocation.objects.create(payment=payment, invoice=invoice, amount=take, created_by=user)
        AuditLog.record(actor=user, action="payment.moved_to_replacement", obj=payment,
                        changes={"from": original.number, "to": invoice.number, "amount": str(take)})
        applied += take
        room -= take
    return applied


def customer_credit(customer) -> Decimal:
    """Money received from this customer that isn't applied to any invoice yet."""
    total = ZERO
    for p in Payment.objects.filter(customer=customer, is_reversed=False).with_allocation_totals():
        total += max(p.unallocated, ZERO)
    return total
