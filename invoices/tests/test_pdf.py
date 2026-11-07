"""Invoice image tests. Contents are checked on the one-page layout the image is
made from, rendered uncompressed so its text can be searched directly in the
bytes; the image itself is checked for format, shape and how it is served."""

import io
import re
import shutil
import tempfile
from decimal import Decimal as D

import pypdfium2
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from reportlab.lib.pagesizes import A4

from core.models import BusinessSettings
from core.testing import configure_business, make_customer, make_product, make_user
from invoices import services
from invoices.pdf import invoice_filename, render_invoice_image, render_invoice_pdf


def pdf_text(invoice) -> str:
    return render_invoice_pdf(invoice, compress=False).read().decode("latin-1")


def page_count(raw: str) -> int:
    return len(re.findall(r"/Type /Page\b", raw))


def text_top(invoice, text) -> tuple[float, float]:
    """Height of the page, and how far up the page the given text sits (in points)."""
    doc = pypdfium2.PdfDocument(render_invoice_pdf(invoice).read())
    try:
        page = doc[0]
        textpage = page.get_textpage()
        found = textpage.search(text).get_next()
        return page.get_height(), textpage.get_charbox(found[0])[3]
    finally:
        doc.close()


def image_size(buffer) -> tuple[int, int]:
    return Image.open(buffer).size


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
                         "KES 240.00", "Pay to till 000000", "14 days", "AMOUNT DUE", "Balance due", f"Invoice {inv.number}"]:
            with self.subTest(expected=expected):
                self.assertIn(expected, raw)
        self.assertNotIn("DRAFT", raw)

    def test_short_invoice_is_a4_with_payment_details_at_the_bottom(self):
        inv = self.issued()
        self.assertEqual(page_count(pdf_text(inv)), 1)
        height, payment_top = text_top(inv, "Pay to till")
        self.assertAlmostEqual(height, A4[1], places=0)
        self.assertLess(payment_top, height * 0.25)  # bottom quarter, not under the totals
        _, terms_top = text_top(inv, "14 days")
        self.assertLess(terms_top, payment_top)

    def test_long_invoice_is_one_taller_page_with_nothing_overlapping(self):
        inv = self.issued(lines=60, qty="1")
        raw = pdf_text(inv)
        self.assertEqual(page_count(raw), 1)
        self.assertEqual(raw.count("(Price)"), 1)
        height, payment_top = text_top(inv, "Pay to till")
        self.assertGreater(height, A4[1])
        _, balance_top = text_top(inv, "Balance due")
        self.assertLess(payment_top, balance_top)  # payment details still below the totals
        self.assertLess(payment_top, height * 0.25)

    def test_very_long_notes_still_fit_on_one_page(self):
        inv = services.create_draft(user=self.user, customer=self.customer, notes="\n".join(["A note"] * 150))
        services.add_line(inv, product=self.soap, quantity="1")
        self.assertEqual(page_count(pdf_text(inv)), 1)

    def test_invoice_without_notes_or_payment_details(self):
        configure_business(payment_instructions="")
        inv = services.create_draft(user=self.user, customer=self.customer, payment_terms="")
        services.add_line(inv, product=self.soap, quantity="1")
        self.assertEqual(page_count(pdf_text(inv)), 1)

    def test_image_is_a_sharp_png_that_grows_with_the_invoice(self):
        short = render_invoice_image(self.issued())
        self.assertTrue(short.getvalue().startswith(b"\x89PNG"))
        width, height = image_size(short)
        self.assertEqual(width, 1654)
        self.assertAlmostEqual(height / width, A4[1] / A4[0], places=2)
        long_w, long_h = image_size(render_invoice_image(self.issued(lines=60, qty="1")))
        self.assertEqual(long_w, 1654)
        self.assertGreater(long_h, height)

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
        r = self.client.get(reverse("invoices:image", args=[inv.pk]), {"download": "1"})
        self.assertEqual(r["Content-Type"], "image/png")
        self.assertIn("no-store", r["Cache-Control"])
        self.assertIn(f'attachment; filename="{inv.number}_test-school.png"', r["Content-Disposition"])
        self.assertTrue(b"".join(r.streaming_content).startswith(b"\x89PNG"))
        r = self.client.get(reverse("invoices:image", args=[inv.pk]))
        self.assertTrue(r["Content-Disposition"].startswith("inline"))
        draft = services.create_draft(user=self.user, customer_name="Walk-in")
        self.assertEqual(invoice_filename(draft), f"DRAFT-{draft.pk}_walk-in.png")
