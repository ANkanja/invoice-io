import io
import shutil
import tempfile
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import AuditLog, BusinessSettings, ImmutableRecordError
from core.testing import configure_business, make_product, make_user
from inventory.services import record_stock_in

TEMP_MEDIA = tempfile.mkdtemp(prefix="invoicing-test-media-")


def png_bytes(size=(40, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (31, 111, 92)).save(buf, format="PNG")
    return buf.getvalue()


VALID_SETTINGS = {
    "name": "Test Business", "phone": "0700 000000", "email": "", "address": "", "kra_pin": "",
    "registration_number": "", "currency": "kes", "invoice_prefix": "inv", "invoice_number_padding": "4",
    "reset_numbering_yearly": "on", "default_payment_terms_days": "", "default_payment_terms": "",
    "default_invoice_notes": "", "payment_instructions": "", "tax_label": "VAT", "tax_rate": "0",
}


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class SettingsPageTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.url = reverse("core:settings")

    def test_page_renders_empty(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Business details")
        self.assertContains(response, 'data-image-preview="logo-preview"')

    def test_save_normalises_and_audits(self):
        response = self.client.post(self.url, VALID_SETTINGS)
        self.assertRedirects(response, self.url)
        s = BusinessSettings.load()
        self.assertEqual((s.name, s.currency, s.invoice_prefix), ("Test Business", "KES", "INV"))
        self.assertTrue(AuditLog.objects.filter(action="settings.updated").exists())

    def test_required_fields(self):
        response = self.client.post(self.url, {**VALID_SETTINGS, "name": "", "phone": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "is-invalid")
        self.assertEqual(BusinessSettings.load().name, "")

    def test_tax_rate_required_when_tax_on(self):
        response = self.client.post(self.url, {**VALID_SETTINGS, "tax_enabled": "on", "tax_rate": "0"})
        self.assertContains(response, "Enter the tax rate")

    def test_tax_rate_range(self):
        response = self.client.post(self.url, {**VALID_SETTINGS, "tax_rate": "150"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BusinessSettings.objects.filter(tax_rate=150).exists())

    def test_logo_upload_and_protected_serving(self):
        logo = SimpleUploadedFile("my logo.PNG", png_bytes(), content_type="image/png")
        self.assertRedirects(self.client.post(self.url, {**VALID_SETTINGS, "logo": logo}), self.url)
        s = BusinessSettings.load()
        self.assertTrue(s.logo.name.startswith("branding/"))
        self.assertNotIn("my logo", s.logo.name)  # random safe file name
        served = self.client.get(s.logo.url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "image/png")
        self.client.logout()
        self.assertEqual(self.client.get(s.logo.url).status_code, 302)

    def test_fake_image_rejected(self):
        fake = SimpleUploadedFile("logo.png", b"MZ\x90\x00 definitely not an image", content_type="image/png")
        response = self.client.post(self.url, {**VALID_SETTINGS, "logo": fake})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "valid image")
        self.assertFalse(BusinessSettings.load().logo)

    @override_settings(MAX_UPLOAD_MB=0)
    def test_oversized_image_rejected(self):
        logo = SimpleUploadedFile("logo.png", png_bytes(), content_type="image/png")
        response = self.client.post(self.url, {**VALID_SETTINGS, "logo": logo})
        self.assertContains(response, "too large")

    def test_settings_cannot_be_deleted_and_stay_single(self):
        s = BusinessSettings.load()
        with self.assertRaises(ImmutableRecordError):
            s.delete()
        BusinessSettings(name="Second").save()
        self.assertEqual(BusinessSettings.objects.count(), 1)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class ProtectedMediaTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user())

    def test_path_traversal_blocked(self):
        for path in ["/media/../.env", "/media/..%2F..%2Fmanage.py", "/media/%2Fetc%2Fpasswd"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_missing_file_404(self):
        self.assertEqual(self.client.get("/media/products/nope.png").status_code, 404)


class DashboardTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_setup_prompt_until_configured(self):
        self.assertContains(self.client.get(reverse("core:dashboard")), "Finish setting up")
        configure_business()
        self.assertNotContains(self.client.get(reverse("core:dashboard")), "Finish setting up")

    def test_empty_state(self):
        self.assertContains(self.client.get(reverse("core:dashboard")), "Low-stock alerts appear here")

    def test_low_stock_listed(self):
        configure_business()
        p = make_product("Dish Wash", low_stock_threshold=D("5"))
        record_stock_in(p, "3", self.user)
        ok = make_product("Bleach", low_stock_threshold=D("5"))
        record_stock_in(ok, "50", self.user)
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, "Dish Wash")
        self.assertNotContains(response, "Bleach")
        self.assertContains(response, "1 product")

    def test_layout_shows_business_name_and_no_dead_links(self):
        configure_business(name="Test Business")
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, "Test Business")
        self.assertContains(response, 'aria-disabled="true"')
        self.assertNotContains(response, 'href="#"')


class PermissionTests(TestCase):
    def test_staff_without_permission_gets_403(self):
        from core.testing import make_staff
        self.client.force_login(make_staff())
        self.assertEqual(self.client.get(reverse("core:settings")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)
        page = self.client.get(reverse("core:dashboard"))
        self.assertNotContains(page, reverse("core:settings"))  # link hidden too
