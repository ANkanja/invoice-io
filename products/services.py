"""Saving, archiving and restoring products and categories."""

from django.db import transaction

from core.models import AuditLog, form_changes
from inventory.services import record_stock_in

from .images import process_product_photo
from .models import Category, Product, SellingOption

AUDITED_FIELDS = ["name", "sku", "category", "description", "stock_unit", "track_stock", "low_stock_threshold"]


def _delete_files_after_commit(*fields) -> None:
    """Delete replaced photo files only once the database change is committed."""
    for field_file in fields:
        if field_file:
            storage, name = field_file.storage, field_file.name
            transaction.on_commit(lambda s=storage, n=name: s.delete(n))


@transaction.atomic
def save_product(form, user) -> Product:
    """Save a ProductForm: process any new photo, record opening stock for new
    products, and write an audit entry. All or nothing."""
    product = form.instance
    is_new = product.pk is None
    before: dict = {}
    old_photo = old_thumb = None
    if not is_new:
        # form.instance already holds the submitted values, so compare with the database copy.
        current = Product.objects.get(pk=product.pk)
        before = {f: getattr(current, f) for f in AUDITED_FIELDS}
        old_photo, old_thumb = current.photo, current.thumbnail

    product = form.save(commit=False)
    upload = form.cleaned_data.get("photo")
    photo_changed = False
    if upload:
        processed = process_product_photo(upload)
        product.photo.save(processed.photo.name, processed.photo, save=False)
        product.thumbnail.save(processed.thumbnail.name, processed.thumbnail, save=False)
        photo_changed = True
    elif form.cleaned_data.get("remove_photo") and product.photo:
        product.photo = ""
        product.thumbnail = ""
        photo_changed = True
    product.save()

    if photo_changed and not is_new:
        _delete_files_after_commit(old_photo, old_thumb)

    if is_new:
        values = getattr(form, "option_values", None)
        if values:
            SellingOption.objects.create(product=product, is_default=True, **values)
        opening = form.cleaned_data.get("opening_stock_base")
        if product.track_stock and opening:
            record_stock_in(product, opening, user, reference="Opening stock",
                            entered=(form.cleaned_data["opening_stock"], form.cleaned_data["opening_stock_label"]))
        AuditLog.record(actor=user, action="product.created", obj=product)
    else:
        changes = form_changes(form, before)
        if str(before.get("low_stock_threshold")) != str(product.low_stock_threshold):
            changes["low_stock_threshold"] = [str(before.get("low_stock_threshold")), str(product.low_stock_threshold)]
        if photo_changed:
            changes["photo"] = ["replaced" if upload else "removed", ""]
        if changes:
            AuditLog.record(actor=user, action="product.updated", obj=product, changes=changes)
    return product


@transaction.atomic
def set_product_active(product: Product, active: bool, user) -> Product:
    if product.is_active != active:
        product.is_active = active
        product.save(update_fields=["is_active", "updated_at"])
        AuditLog.record(actor=user, action="product.restored" if active else "product.archived", obj=product)
    return product


@transaction.atomic
def save_category(form, user) -> Category:
    is_new = form.instance.pk is None
    before = {} if is_new else {"name": Category.objects.get(pk=form.instance.pk).name}
    category = form.save()
    if is_new:
        AuditLog.record(actor=user, action="category.created", obj=category)
    elif changes := form_changes(form, before):
        AuditLog.record(actor=user, action="category.updated", obj=category, changes=changes)
    return category


@transaction.atomic
def set_category_active(category: Category, active: bool, user) -> Category:
    if category.is_active != active:
        category.is_active = active
        category.save(update_fields=["is_active", "updated_at"])
        AuditLog.record(actor=user, action="category.restored" if active else "category.archived", obj=category)
    return category


@transaction.atomic
def save_option(product: Product, values: dict, user, *, instance: SellingOption | None = None,
                is_default: bool = False, is_active: bool = True) -> SellingOption:
    """Create or update a selling option. Exactly one active option is the default.
    Existing invoices keep their own copy of the option's label, units and price."""
    product = Product.objects.select_for_update().get(pk=product.pk)
    option = instance or SellingOption(product=product)
    before = None if instance is None else {"label": instance.label, "price": str(instance.price)}
    for key, value in values.items():
        setattr(option, key, value)
    option.is_active = is_active
    if not is_active:
        is_default = False
    if is_default:
        product.options.exclude(pk=option.pk).filter(is_default=True).update(is_default=False)
    option.is_default = is_default
    option.full_clean(exclude=["product"])
    option.save()
    # Keep one default among the active options.
    active = product.options.filter(is_active=True)
    if active.exists() and not active.filter(is_default=True).exists():
        first = active.order_by("sort_order", "pk").first()
        SellingOption.objects.filter(pk=first.pk).update(is_default=True)
    AuditLog.record(actor=user, action="product.option_saved", obj=product,
                    changes={"option": option.label, "price": str(option.price), "before": before or ""})
    return option
