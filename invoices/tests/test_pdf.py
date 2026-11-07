"""PDF tests. PDFs are rendered uncompressed here so the text drawn on each page
can be searched directly in the file's bytes (no extra PDF library needed)."""

import io
import re
import shutil
import tempfile
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import BusinessSettings
from core.testing import configure_business, make_customer, make_product, make_user
from invoices import services
from invoices.pdf import invoice_filename, render_invoice_pdf


def pdf_text(invoice) -> str:
    return render_invoice_pdf(invoice, compress=False).read().decode("latin-1")


def page_count(raw: str) -> int:
    return len(re.findall(r"/Type /Page\b", raw))


class InvoicePdfTests(TestCase):
    def setUp(self):
        self.user = make_user()
        configure_business(name="Test Business", phone="0700 000000", payment_instructions="Pay to till 000000")
        self.customer = make_customer("Test School", kind="organization", tax_pin="P000000000X")
        self.soap = make_product("Bar soap", "80.00", package_size="200 g", unit="bar", track_stock=False)

    def issued(self, lines=1, qty="3"):
        inv = services.create_draft(user=self.user, customer=self.customer, payment_terms="14 days")
        for _ in range(lines):
            services.add_line(inv, product=self.soap, quantity=qty)
        return services.issue_invoice(inv, self.user)

    def test_contents(self):
        inv = self.issued()
        raw = pdf_text(inv)
        for expected in [inv.number, "Test Business", "Test School", "P000000000X", "Bar soap", "200 g bar",
                         "KES 240.00", "Pay to till 000000", "14 days", "AMOUNT DUE", "Balance due", "Page 1 of 1"]:
            with self.subTest(expected=expected):
                self.assertIn(expected, raw)
        self.assertNotIn("DRAFT", raw)

    def test_long_invoice_spans_pages_with_numbering(self):
        inv = self.issued(lines=60, qty="1")
        raw = pdf_text(inv)
        pages = page_count(raw)
        self.assertGreaterEqual(pages, 2)
        self.assertIn(f"Page {pages} of {pages}", raw)
        self.assertEqual(raw.count("(Price)"), pages)  # table header repeated on every page

    def test_draft_and_cancelled_are_watermarked(self):
        draft = services.create_draft(user=self.user, customer=self.customer)
        services.add_line(draft, product=self.soap, quantity="1")
        raw = pdf_text(draft)
        self.assertIn("DRAFT INVOICE", raw)
        self.assertIn("Not issued yet", raw)
        inv = services.cancel_invoice(self.issued(), self.user, "Ordered twice")
        raw = pdf_text(inv)
        self.assertIn("CANCELLED", raw)
        self.assertIn("Ordered twice", raw)

    def test_issued_pdf_keeps_seller_details_from_issue_time(self):
        inv = self.issued()
        s = BusinessSettings.load()
        s.name = "Renamed Business"
        s.save()
        self.assertIn("Test Business", pdf_text(inv))
        self.assertNotIn("Renamed Business", pdf_text(inv))

    def test_user_text_is_escaped(self):
        inv = services.create_draft(user=self.user, customer_name="A & B <Ltd>")
        services.add_line(inv, description="Soap <b>bold</b> & co", quantity="1", unit_price="10")
        raw = pdf_text(inv)  # must not crash on markup characters
        self.assertIn("A & B <Ltd>", raw)

    def test_tax_and_discount_rows(self):
        configure_business(tax_enabled=True, tax_rate=D("16"))
        inv = services.create_draft(user=self.user, customer=self.customer, invoice_discount=D("40"))
        services.add_line(inv, product=self.soap, quantity="3")  # 240 - 40 = 200, VAT 32
        inv = services.issue_invoice(inv, self.user)
        raw = pdf_text(inv)
        self.assertIn("VAT 16%", raw)
        self.assertIn("KES 32.00", raw)
        self.assertIn("- KES 40.00", raw)
        self.assertIn("KES 232.00", raw)

    def test_logo_is_embedded(self):
        media = tempfile.mkdtemp(prefix="invoicing-test-pdf-")
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        with override_settings(MEDIA_ROOT=media):
            self._check_logo()

    def _check_logo(self):
        buf = io.BytesIO()
        Image.new("RGBA", (300, 100), (31, 111, 92, 255)).save(buf, "PNG")
        s = BusinessSettings.load()
        s.logo.save("logo.png", SimpleUploadedFile("logo.png", buf.getvalue()), save=True)
        inv = self.issued()
        self.assertIn("/Subtype /Image", pdf_text(inv))

    def test_download_view_and_filename(self):
        inv = self.issued()
        self.client.force_login(self.user)
        r = self.client.get(reverse("invoices:pdf", args=[inv.pk]), {"download": "1"})
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertIn(f'attachment; filename="{inv.number}_test-school.pdf"', r["Content-Disposition"])
        self.assertTrue(b"".join(r.streaming_content).startswith(b"%PDF"))
        r = self.client.get(reverse("invoices:pdf", args=[inv.pk]))
        self.assertTrue(r["Content-Disposition"].startswith("inline"))
        draft = services.create_draft(user=self.user, customer_name="Walk-in")
        self.assertEqual(invoice_filename(draft), f"DRAFT-{draft.pk}_walk-in.pdf")
