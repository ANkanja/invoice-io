import io
import shutil
import tempfile
from decimal import Decimal as D

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import AuditLog
from core.testing import configure_business, make_option, make_product, make_staff, make_user, unit
from inventory.models import StockMovement
from inventory.services import record_stock_in
from products.models import Category, Product, SellingOption

TEMP_MEDIA = tempfile.mkdtemp(prefix="invoicing-test-products-")


def jpeg_with_exif(size=(3000, 2000)) -> bytes:
    """A big 'phone photo' with orientation and GPS metadata."""
    img = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6            # Orientation: rotate 90° clockwise to display
    exif[0x8825] = {1: "S", 2: (1.0, 17.0, 0.0)}  # GPSInfo (Nairobi-ish latitude)
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def png_with_alpha() -> bytes:
    img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    img.paste((31, 111, 92, 255), (100, 50, 300, 250))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def valid(**overrides) -> dict:
    """A new product: liquid soap kept in ml, sold as a 5 L container at KES 650."""
    data = {
        "name": "Liquid hand soap", "category": "", "sku": "", "description": "",
        "stock_unit": unit("ml").pk, "track_stock": "on", "low_stock_alert": "20",
        "opening_stock": "", "opening_stock_unit": unit("l").pk,
        "option-method": "package", "option-package_unit": unit("container").pk,
        "option-content_quantity": "5", "option-content_unit": unit("l").pk, "option-price_unit": unit("l").pk,
        "option-price": "650.00", "option-cost_price": "", "option-label": "",
    }
    data.update(overrides)
    return data


def edit_data(product, **overrides) -> dict:
    data = {"name": product.name, "category": product.category_id or "", "sku": product.sku or "",
            "description": product.description, "stock_unit": product.stock_unit_id,
            "track_stock": "on" if product.track_stock else "", "low_stock_alert": ""}
    data.update(overrides)
    return data


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class ProductViewTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.user = make_user()
        configure_business()
        self.client.force_login(self.user)

    # --- list --------------------------------------------------------------
    def test_empty_state(self):
        self.assertContains(self.client.get(reverse("products:list")), "No products yet")

    def test_search_and_filters(self):
        cat = Category.objects.create(name="Bar soaps")
        bar = make_product("Bar soap", category=cat, package_size="200 g", low_stock_threshold=D("10"))
        make_product("Bleach", package_size="1 L")
        old = make_product("Old cleaner", package_size="1 L", is_active=False)
        record_stock_in(bar, "3", self.user)
        url = reverse("products:list")
        def names(**params):
            return [p.name for p in self.client.get(url, params).context["page"].object_list]

        self.assertEqual(names(q="blea"), ["Bleach"])
        self.assertEqual(names(q="200 g"), ["Bar soap"])  # size is searchable too
        self.assertEqual(names(category=cat.pk), ["Bar soap"])
        self.assertEqual(names(category="none"), ["Bleach"])
        self.assertEqual(names(stock="low"), ["Bar soap"])
        self.assertEqual(names(stock="out"), ["Bleach"])  # tracked, zero stock
        self.assertEqual(names(), ["Bar soap", "Bleach"])
        self.assertEqual(names(status="archived"), [old.name])
        self.assertEqual(len(names(status="all")), 3)
        self.assertEqual(self.client.get(url, {"category": "junk", "page": "999"}).status_code, 200)

    def test_pagination(self):
        for i in range(30):
            make_product(f"Product {i:02d}", package_size="")
        r = self.client.get(reverse("products:list"))
        self.assertContains(r, "1–25 of 30")
        self.assertContains(self.client.get(reverse("products:list"), {"page": 2}), "Product 29")

    # --- create / edit -----------------------------------------------------
    def test_create_with_opening_stock(self):
        r = self.client.post(reverse("products:create"), valid(**{
            "opening_stock": "12", "option-cost_price": "400", "low_stock_alert": "10"}))
        product = Product.objects.get()
        self.assertRedirects(r, reverse("products:detail", args=[product.pk]))
        # Stock is kept in ml; 12 L opening stock, alert at 10 L.
        self.assertEqual((product.stock_unit.code, product.stock_qty), ("ml", D("12000")))
        self.assertEqual(product.low_stock_threshold, D("10000"))
        self.assertEqual(product.stock_display, "12 L")
        option = product.default_option
        self.assertEqual((option.label, option.content_quantity, option.price, option.cost_price),
                         ("5 L container", D("5000"), D("650.00"), D("400.00")))
        m = StockMovement.objects.get()
        self.assertEqual((m.kind, m.reference, m.entered_quantity, m.entered_unit),
                         ("stock_in", "Opening stock", D("12"), "L"))
        self.assertTrue(AuditLog.objects.filter(action="product.created").exists())

    def test_create_priced_by_measure(self):
        self.client.post(reverse("products:create"), valid(**{
            "option-method": "measure", "option-price_unit": unit("l").pk, "option-price": "250"}))
        option = Product.objects.get().default_option
        self.assertEqual((option.method, option.price_unit.code, option.label), ("measure", "l", "per litre"))

    def test_save_and_add_another(self):
        r = self.client.post(reverse("products:create"), valid(add_another="1"))
        self.assertRedirects(r, reverse("products:create"))

    def test_validation_messages(self):
        make_product("Liquid hand soap")
        r = self.client.post(reverse("products:create"), valid(name="Bleach", **{"option-price": "-1"}))
        self.assertContains(r, "greater than or equal to 0")
        r = self.client.post(reverse("products:create"), valid())
        self.assertContains(r, "You already have a product with this name.")
        r = self.client.post(reverse("products:create"), valid(name="Bleach", track_stock="", opening_stock="5"))
        self.assertContains(r, "Tick “Track stock” to record opening stock.")
        # A pack measured in kg can't hold stock counted in ml.
        r = self.client.post(reverse("products:create"), valid(name="Bleach", **{"option-content_unit": unit("kg").pk}))
        self.assertContains(r, "can&#x27;t be converted")
        self.assertEqual(Product.objects.count(), 1)

    def test_edit_records_audit_and_never_touches_stock(self):
        p = make_product("Bleach", "120.00", package_size="1 L")
        record_stock_in(p, "9", self.user)
        r = self.client.post(reverse("products:edit", args=[p.pk]), edit_data(p, name="Thick bleach"))
        self.assertRedirects(r, reverse("products:detail", args=[p.pk]))
        p.refresh_from_db()
        self.assertEqual((p.name, p.stock_qty), ("Thick bleach", D("9")))
        entry = AuditLog.objects.get(action="product.updated")
        self.assertEqual(entry.changes["name"], ["Bleach", "Thick bleach"])
        form = self.client.get(reverse("products:edit", args=[p.pk])).context["form"]
        self.assertNotIn("opening_stock", form.fields)
        self.assertTrue(form.fields["stock_unit"].disabled)  # has stock history

    def test_add_and_edit_selling_options(self):
        p = make_product("Liquid soap", stock_unit="ml")
        make_option(p, pack="container", content="5000", price="650", label="5 L container", default=True)
        url = reverse("products:option_create", args=[p.pk])
        r = self.client.post(url, {"method": "measure", "price_unit": unit("l").pk, "price": "140", "label": "",
                                   "is_active": "on"})
        self.assertRedirects(r, reverse("products:detail", args=[p.pk]))
        loose = SellingOption.objects.get(product=p, method="measure")
        self.assertEqual((loose.label, loose.is_default), ("per litre", False))
        # Change the price and make it the default; the old default steps down.
        edit = reverse("products:option_edit", args=[p.pk, loose.pk])
        self.client.post(edit, {"method": "measure", "price_unit": unit("l").pk, "price": "150", "label": "loose",
                                "is_default": "on", "is_active": "on"})
        loose.refresh_from_db()
        self.assertEqual((loose.price, loose.label, loose.is_default), (D("150.00"), "loose", True))
        self.assertEqual(p.options.filter(is_default=True).count(), 1)
        # Counting units must match the stock: a "per kg" price is refused for a product kept in ml.
        r = self.client.post(url, {"method": "measure", "price_unit": unit("kg").pk, "price": "1", "is_active": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(p.options.count(), 2)

    def test_untracking_clears_threshold(self):
        self.client.post(reverse("products:create"), valid(track_stock=""))
        self.assertIsNone(Product.objects.get().low_stock_threshold)

    # --- photos -----------------------------------------------------------
    def test_photo_is_resized_rotated_and_stripped(self):
        upload = SimpleUploadedFile("IMG_2024.jpg", jpeg_with_exif(), content_type="image/jpeg")
        self.client.post(reverse("products:create"), valid(photo=upload))
        p = Product.objects.get()
        with default_storage.open(p.photo.name) as f, Image.open(f) as img:
            self.assertEqual(img.format, "JPEG")
            self.assertEqual(max(img.size), 1600)
            self.assertGreater(img.height, img.width)  # rotated upright (was landscape + orientation 6)
            self.assertNotIn(0x8825, img.getexif())    # GPS removed
            self.assertNotIn(0x0112, img.getexif())
        with default_storage.open(p.thumbnail.name) as f, Image.open(f) as thumb:
            self.assertEqual(thumb.format, "WEBP")
            self.assertLessEqual(max(thumb.size), 320)
        self.assertNotIn("IMG_2024", p.photo.name)
        self.assertContains(self.client.get(reverse("products:list")), p.thumbnail.url)

    def test_transparent_png_stays_png(self):
        upload = SimpleUploadedFile("cutout.png", png_with_alpha(), content_type="image/png")
        self.client.post(reverse("products:create"), valid(photo=upload))
        with default_storage.open(Product.objects.get().photo.name) as f, Image.open(f) as img:
            self.assertEqual((img.format, img.mode), ("PNG", "RGBA"))

    def test_replace_and_remove_photo_deletes_old_files(self):
        self.client.post(reverse("products:create"), valid(photo=SimpleUploadedFile("a.png", png_with_alpha(), content_type="image/png")))
        p = Product.objects.get()
        first = (p.photo.name, p.thumbnail.name)
        edit = reverse("products:edit", args=[p.pk])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(edit, edit_data(p, photo=SimpleUploadedFile("b.jpg", jpeg_with_exif((800, 600)), content_type="image/jpeg")))
        p.refresh_from_db()
        self.assertNotEqual(p.photo.name, first[0])
        self.assertFalse(any(default_storage.exists(n) for n in first))
        second = p.photo.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(edit, edit_data(p, remove_photo="on"))
        p.refresh_from_db()
        self.assertFalse(p.photo)
        self.assertFalse(default_storage.exists(second))

    def test_fake_photo_rejected(self):
        fake = SimpleUploadedFile("x.jpg", b"not an image at all", content_type="image/jpeg")
        r = self.client.post(reverse("products:create"), valid(photo=fake))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Product.objects.exists())

    # --- archive ----------------------------------------------------------
    def test_archive_and_restore(self):
        p = make_product()
        self.assertEqual(self.client.get(reverse("products:archive", args=[p.pk])).status_code, 405)
        self.client.post(reverse("products:archive", args=[p.pk]))
        p.refresh_from_db()
        self.assertFalse(p.is_active)
        self.assertContains(self.client.get(reverse("products:detail", args=[p.pk])), "Restore product")
        self.client.post(reverse("products:restore", args=[p.pk]))
        p.refresh_from_db()
        self.assertTrue(p.is_active)
        self.assertEqual(AuditLog.objects.filter(action__in=["product.archived", "product.restored"]).count(), 2)

    def test_detail_404(self):
        self.assertEqual(self.client.get(reverse("products:detail", args=[999])).status_code, 404)


class ProductPermissionTests(TestCase):
    def test_view_only_staff(self):
        p = make_product()
        self.client.force_login(make_staff(perms=("products.view_product",)))
        self.assertEqual(self.client.get(reverse("products:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("products:detail", args=[p.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("products:create")).status_code, 403)
        self.assertEqual(self.client.post(reverse("products:archive", args=[p.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("products:list")), "Add product")

    def test_no_permission(self):
        self.client.force_login(make_staff())
        self.assertEqual(self.client.get(reverse("products:list")).status_code, 403)


class CategoryViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_create_rename_archive(self):
        url = reverse("products:categories")
        self.assertRedirects(self.client.post(url, {"name": "  Liquid   soaps "}), url)
        cat = Category.objects.get()
        self.assertEqual(cat.name, "Liquid soaps")
        r = self.client.post(url, {"name": "LIQUID SOAPS"})
        self.assertContains(r, "A category with this name already exists.")
        self.client.post(reverse("products:category_edit", args=[cat.pk]), {"name": "Liquid soap"})
        cat.refresh_from_db()
        self.assertEqual(cat.name, "Liquid soap")
        self.client.post(reverse("products:category_archive", args=[cat.pk]))
        cat.refresh_from_db()
        self.assertFalse(cat.is_active)
        # archived categories aren't offered for new products
        form = self.client.get(reverse("products:create")).context["form"]
        self.assertNotIn(cat, form.fields["category"].queryset)

    def test_archived_category_kept_on_existing_product(self):
        cat = Category.objects.create(name="Old", is_active=False)
        p = make_product(category=cat)
        form = self.client.get(reverse("products:edit", args=[p.pk])).context["form"]
        self.assertIn(cat, form.fields["category"].queryset)
