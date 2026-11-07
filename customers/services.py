from django.db import transaction

from core.models import AuditLog, form_changes

from .models import Customer

AUDITED_FIELDS = ["kind", "name", "contact_person", "phone", "email", "billing_address", "tax_pin"]


@transaction.atomic
def save_customer(form, user) -> Customer:
    is_new = form.instance.pk is None
    before = {} if is_new else {f: getattr(Customer.objects.get(pk=form.instance.pk), f) for f in AUDITED_FIELDS}
    customer = form.save()
    if is_new:
        AuditLog.record(actor=user, action="customer.created", obj=customer)
    elif changes := form_changes(form, before):
        AuditLog.record(actor=user, action="customer.updated", obj=customer, changes=changes)
    return customer


@transaction.atomic
def set_customer_active(customer: Customer, active: bool, user) -> Customer:
    if customer.is_active != active:
        customer.is_active = active
        customer.save(update_fields=["is_active", "updated_at"])
        AuditLog.record(actor=user, action="customer.restored" if active else "customer.archived", obj=customer)
    return customer
