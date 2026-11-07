"""
Invoice PDF, generated on the server with ReportLab from the stored invoice.

* Issued and cancelled invoices use the seller details frozen at issue time
  (Invoice.business_snapshot), so re-downloading years later gives the same
  document even if Settings have changed.
* Drafts use the current Settings and carry a large "DRAFT" watermark; cancelled
  invoices carry "CANCELLED"; earlier versions of an amended invoice carry
  "SUPERSEDED". None can be mistaken for the live invoice.
* Quantities print as entered with their unit ("1,500 ml", "5 bottles") and
  prices with what they are per ("KES 250.00 per L").
* Long invoices flow onto more pages; the table header repeats on each page and
  every page shows "Page x of y".
* All user-entered text is escaped before it reaches ReportLab's markup parser.
"""

from io import BytesIO
from xml.sax.saxutils import escape

from django.core.files.storage import default_storage
from django.utils.text import slugify
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.models import BusinessSettings
from core.money import format_money, format_quantity

from .models import Invoice

EUCALYPTUS = colors.HexColor("#1F6F5C")
INK = colors.HexColor("#1C2B28")
MUTED = colors.HexColor("#5B6B67")
LINE = colors.HexColor("#D8E1DD")
TINT = colors.HexColor("#E3EFEB")
BRICK = colors.HexColor("#B5432F")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
FRAME_W = PAGE_W - 2 * MARGIN

BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=12.5, textColor=INK)
SMALL = ParagraphStyle("small", parent=BASE, fontSize=8.5, leading=11, textColor=MUTED)
BOLD = ParagraphStyle("bold", parent=BASE, fontName="Helvetica-Bold")
SELLER = ParagraphStyle("seller", parent=BASE, fontName="Helvetica-Bold", fontSize=14, leading=17)
TITLE = ParagraphStyle("title", parent=BASE, fontName="Helvetica-Bold", fontSize=22, leading=26,
                       textColor=EUCALYPTUS, alignment=TA_RIGHT)
LABEL = ParagraphStyle("label", parent=SMALL, fontName="Helvetica-Bold", textColor=MUTED)
RIGHT = ParagraphStyle("right", parent=BASE, alignment=TA_RIGHT)
RIGHT_BOLD = ParagraphStyle("right_bold", parent=BOLD, alignment=TA_RIGHT)
CELL = ParagraphStyle("cell", parent=BASE, alignment=TA_LEFT)
HEAD = ParagraphStyle("head", parent=BASE, fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.white)
HEAD_R = ParagraphStyle("head_r", parent=HEAD, alignment=TA_RIGHT)


def _p(text, style=BASE) -> Paragraph:
    """Paragraph from plain user text: escaped, newlines kept."""
    return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), style)


def invoice_filename(invoice: Invoice) -> str:
    ref = invoice.number or f"DRAFT-{invoice.pk}"
    customer = slugify(invoice.customer_name)[:40] or "customer"
    return f"{ref}_{customer}.pdf"


def _seller(invoice: Invoice) -> dict:
    if invoice.status != Invoice.Status.DRAFT and invoice.business_snapshot:
        return invoice.business_snapshot
    return BusinessSettings.load().snapshot()


def _logo(seller: dict):
    name = seller.get("logo") or ""
    if not name or not default_storage.exists(name):
        return None
    try:
        with default_storage.open(name, "rb") as fh:
            data = BytesIO(fh.read())
        width, height = ImageReader(data).getSize()
        data.seek(0)
    except Exception:  # unreadable logo: leave it out rather than fail the invoice
        return None
    max_w, max_h = 48 * mm, 20 * mm
    scale = min(max_w / width, max_h / height)
    img = Image(data, width=width * scale, height=height * scale)
    img.hAlign = "LEFT"
    return img


def _make_canvas(invoice: Invoice, watermark: str):
    """Canvas class that draws the footer ("Page x of y") and the watermark."""
    ref = invoice.number or "Draft (not issued)"

    class InvoiceCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total):
            self.saveState()
            if watermark:
                self.setFont("Helvetica-Bold", 96)
                self.setFillColor(MUTED if watermark == "DRAFT" else BRICK)
                if len(watermark) > 9:
                    self.setFont("Helvetica-Bold", 72)
                self.setFillAlpha(0.10)
                self.translate(PAGE_W / 2, PAGE_H / 2)
                self.rotate(35)
                self.drawCentredString(0, -30, watermark)
                self.rotate(-35)
                self.translate(-PAGE_W / 2, -PAGE_H / 2)
                self.setFillAlpha(1)
            self.setStrokeColor(LINE)
            self.setLineWidth(0.6)
            self.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
            self.setFont("Helvetica", 8)
            self.setFillColor(MUTED)
            self.drawString(MARGIN, 10 * mm, f"Invoice {ref}")
            self.drawRightString(PAGE_W - MARGIN, 10 * mm, f"Page {self._pageNumber} of {total}")
            self.restoreState()

    return InvoiceCanvas


def _header(invoice: Invoice, seller: dict, money, superseded_on=None):
    is_draft = invoice.status == Invoice.Status.DRAFT
    left = []
    if logo := _logo(seller):
        left += [logo, Spacer(1, 3 * mm)]
    left.append(_p(seller.get("name") or "Business name not set", SELLER))
    for line in [seller.get("address"), seller.get("phone"), seller.get("email")]:
        if line:
            left.append(_p(line, SMALL))
    if seller.get("kra_pin"):
        left.append(_p(f"KRA PIN: {seller['kra_pin']}", SMALL))
    if seller.get("registration_number"):
        left.append(_p(f"Reg. no.: {seller['registration_number']}", SMALL))

    meta = [
        [_p("Invoice no.", LABEL), _p(invoice.number or "Not issued yet", RIGHT_BOLD)],
        [_p("Date", LABEL), _p(invoice.issue_date.strftime("%d %b %Y"), RIGHT)],
    ]
    if invoice.due_date:
        meta.append([_p("Due", LABEL), _p(invoice.due_date.strftime("%d %b %Y"), RIGHT)])
    if invoice.revision and invoice.revision > 1 and invoice.last_amended_at and not superseded_on:
        meta.append([_p("Revised", LABEL), _p(f"{invoice.last_amended_at.strftime('%d %b %Y')} (rev. {invoice.revision})", RIGHT)])
    if superseded_on:
        meta.append([_p("Version", LABEL), _p(f"Revision {invoice.revision}", RIGHT)])
        meta.append([_p("Status", LABEL), _p(f"SUPERSEDED {superseded_on.strftime('%d %b %Y')}",
                                              ParagraphStyle("s", parent=RIGHT_BOLD, textColor=BRICK))])
    elif invoice.status == Invoice.Status.CANCELLED:
        meta.append([_p("Status", LABEL), _p("CANCELLED", ParagraphStyle("c", parent=RIGHT_BOLD, textColor=BRICK))])
    meta_table = Table(meta, colWidths=[24 * mm, 44 * mm], hAlign="RIGHT")
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                    ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    right = [_p("DRAFT INVOICE" if is_draft else "INVOICE", TITLE), Spacer(1, 3 * mm), meta_table]

    header = Table([[left, right]], colWidths=[FRAME_W * 0.55, FRAME_W * 0.45])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return header


def _bill_to(invoice: Invoice, balance, money, show_payments=True):
    who = [_p("BILL TO", LABEL), Spacer(1, 1 * mm), _p(invoice.customer_name, BOLD)]
    for line in [invoice.customer_contact_person and f"Attn: {invoice.customer_contact_person}",
                 invoice.customer_address, invoice.customer_phone, invoice.customer_email]:
        if line:
            who.append(_p(line, SMALL))
    if invoice.customer_tax_pin:
        who.append(_p(f"KRA PIN: {invoice.customer_tax_pin}", SMALL))

    due = show_payments and invoice.status == Invoice.Status.ISSUED
    amount_label = "AMOUNT DUE" if due else "TOTAL"
    amount = balance if due else invoice.total
    box = Table([[_p(amount_label, ParagraphStyle("al", parent=LABEL, textColor=EUCALYPTUS, alignment=TA_RIGHT))],
                 [_p(money(amount), ParagraphStyle("av", parent=RIGHT_BOLD, fontSize=16, leading=20))]],
                colWidths=[60 * mm], hAlign="RIGHT")
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (0, 0), 6),
                             ("BOTTOMPADDING", (0, -1), (-1, -1), 7)]))
    t = Table([[who, box]], colWidths=[FRAME_W * 0.6, FRAME_W * 0.4])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _lines_table(invoice: Invoice, lines, money):
    show_discount = any(line.line_discount for line in lines)
    head = [_p("#", HEAD), _p("Description", HEAD), _p("Qty", HEAD_R), _p("Price", HEAD_R)]
    widths = [8 * mm, None, 24 * mm, 30 * mm]
    if show_discount:
        head.append(_p("Discount", HEAD_R))
        widths.append(24 * mm)
    head.append(_p("Amount", HEAD_R))
    widths.append(30 * mm)
    widths[1] = FRAME_W - sum(w for w in widths if w)

    rows = [head]
    for i, line in enumerate(lines, start=1):
        detail = line.package_size if line.package_size and line.package_size != line.description else ""
        desc = escape(line.description)
        if detail:
            desc += f'<br/><font size="8" color="#5B6B67">{escape(detail)}</font>'
        price = escape(money(line.unit_price))
        if per := line.price_display_unit:
            price += f'<br/><font size="8" color="#5B6B67">{escape(per)}</font>'
        row = [_p(i, SMALL), Paragraph(desc, CELL), _p(line.quantity_display, RIGHT), Paragraph(price, RIGHT)]
        if show_discount:
            row.append(_p(money(line.line_discount) if line.line_discount else "", RIGHT))
        row.append(_p(money(line.line_total), RIGHT))
        rows.append(row)

    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), EUCALYPTUS),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAF9")]),
    ]))
    return table


def _totals(invoice: Invoice, amount_paid, balance, money, show_payments=True):
    rows = [["Subtotal", money(invoice.subtotal)]]
    if invoice.invoice_discount:
        rows.append(["Discount", f"- {money(invoice.invoice_discount)}"])
    if invoice.tax_rate:
        label = f"{invoice.tax_label or 'Tax'} {format_quantity(invoice.tax_rate)}%"
        if invoice.prices_include_tax:
            label += " (included)"
        rows.append([label, money(invoice.tax_total)])
    rows.append(["Total", money(invoice.total)])
    total_row = len(rows) - 1
    if show_payments and invoice.status == Invoice.Status.ISSUED:
        rows.append(["Paid", money(amount_paid)])
        rows.append(["Balance due", money(balance)])
    data = [[_p(a, RIGHT), _p(b, RIGHT)] for a, b in rows]
    for idx in {total_row, len(rows) - 1}:
        data[idx] = [_p(rows[idx][0], RIGHT_BOLD), _p(rows[idx][1], RIGHT_BOLD)]
    t = Table(data, colWidths=[40 * mm, 36 * mm], hAlign="RIGHT")
    t.setStyle(TableStyle([("LINEABOVE", (0, total_row), (-1, total_row), 0.8, INK),
                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _notes(invoice: Invoice, seller: dict):
    blocks = []
    if invoice.status == Invoice.Status.CANCELLED:
        when = invoice.cancelled_at.strftime("%d %b %Y") if invoice.cancelled_at else ""
        blocks.append(KeepTogether([_p("CANCELLED", ParagraphStyle("cx", parent=LABEL, textColor=BRICK)),
                                    _p(f"Cancelled on {when}. Reason: {invoice.cancel_reason}", BASE)]))
    for title, text in [("PAYMENT DETAILS", seller.get("payment_instructions")),
                        ("PAYMENT TERMS", invoice.payment_terms), ("NOTES", invoice.notes)]:
        if text:
            blocks.append(KeepTogether([_p(title, LABEL), Spacer(1, 1 * mm), _p(text, BASE), Spacer(1, 4 * mm)]))
    return blocks


def render_invoice_pdf(invoice: Invoice, *, compress: bool = True, lines=None, superseded_on=None) -> BytesIO:
    """PDF of an invoice. For an earlier version of an amended invoice pass the
    rebuilt invoice and lines (see InvoiceRevision.as_invoice) and the date it
    was superseded; it prints without payments and with a SUPERSEDED mark."""
    lines = list(invoice.lines.all()) if lines is None else lines
    seller = _seller(invoice)
    currency = invoice.currency or "KES"

    def money(value):
        return format_money(value, currency)

    show_payments = superseded_on is None
    amount_paid = invoice.get_amount_paid() if show_payments else 0
    balance = invoice.total - amount_paid

    if superseded_on:
        watermark = "SUPERSEDED"
    else:
        watermark = {Invoice.Status.DRAFT: "DRAFT", Invoice.Status.CANCELLED: "CANCELLED"}.get(invoice.status, "")
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=22 * mm,
        title=f"Invoice {invoice.number or '(draft)'}", author=seller.get("name") or "",
        subject=f"Invoice for {invoice.customer_name}", creator="invoicing_io", pageCompression=1 if compress else 0,
    )
    story = [
        _header(invoice, seller, money, superseded_on), Spacer(1, 9 * mm),
        _bill_to(invoice, balance, money, show_payments), Spacer(1, 8 * mm),
        _lines_table(invoice, lines, money), Spacer(1, 4 * mm),
        KeepTogether(_totals(invoice, amount_paid, balance, money, show_payments)), Spacer(1, 8 * mm),
        *_notes(invoice, seller),
    ]
    doc.build(story, canvasmaker=_make_canvas(invoice, watermark))
    buffer.seek(0)
    return buffer
