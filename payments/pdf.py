"""
Payment receipt PDF (ReportLab), in the same style as the invoice PDF.

Shows who paid, how much, when, how, the reference, and which invoices the
money went to (with each invoice's balance now). Reversed payments carry a
large "REVERSED" mark so a receipt can't be reused.
"""

from io import BytesIO

from django.utils import timezone
from django.utils.text import slugify
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from core.models import BusinessSettings
from core.money import format_money
from invoices.pdf import (
    BASE, BOLD, BRICK, EUCALYPTUS, HEAD, HEAD_R, LABEL, LINE, MUTED, RIGHT, RIGHT_BOLD, SELLER, SMALL, TINT, _logo, _p,
)

PAGE_W, PAGE_H = A5
MARGIN = 14 * mm
FRAME_W = PAGE_W - 2 * MARGIN
TITLE = ParagraphStyle("rtitle", parent=RIGHT_BOLD, fontSize=17, leading=21, textColor=EUCALYPTUS)


def receipt_number(payment) -> str:
    return f"PAY-{payment.pk:06d}"


def receipt_filename(payment) -> str:
    payer = slugify(payment.payer_name)[:40] or "payer"
    return f"Receipt_{receipt_number(payment)}_{payer}.pdf"


def render_receipt_pdf(payment, *, compress: bool = True) -> BytesIO:
    seller = BusinessSettings.load().snapshot()
    allocations = list(payment.allocations.filter(released_at__isnull=True).select_related("invoice"))
    buffer = BytesIO()

    def decorate(canvas, doc):
        canvas.saveState()
        if payment.is_reversed:
            canvas.setFont("Helvetica-Bold", 64)
            canvas.setFillColor(BRICK)
            canvas.setFillAlpha(0.12)
            canvas.translate(PAGE_W / 2, PAGE_H / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, -20, "REVERSED")
            canvas.restoreState()
            canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 9 * mm, f"Receipt {receipt_number(payment)}")
        canvas.drawRightString(PAGE_W - MARGIN, 9 * mm, f"Printed {timezone.localdate():%d %b %Y}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=A5, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=16 * mm,
        title=f"Receipt {receipt_number(payment)}", author=seller.get("name") or "", creator="invoicing_io",
        pageCompression=1 if compress else 0,
    )

    left = []
    if logo := _logo(seller):
        left += [logo, Spacer(1, 2 * mm)]
    left.append(_p(seller.get("name") or "Business name not set", SELLER))
    for line in [seller.get("address"), seller.get("phone"), seller.get("email")]:
        if line:
            left.append(_p(line, SMALL))
    meta = Table([
        [_p("Receipt no.", LABEL), _p(receipt_number(payment), RIGHT_BOLD)],
        [_p("Date paid", LABEL), _p(payment.payment_date.strftime("%d %b %Y"), RIGHT)],
    ], colWidths=[20 * mm, 32 * mm], hAlign="RIGHT")
    meta.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    head = Table([[left, [_p("RECEIPT", TITLE), Spacer(1, 2 * mm), meta]]], colWidths=[FRAME_W * 0.55, FRAME_W * 0.45])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))

    amount_box = Table([
        [_p("RECEIVED FROM", LABEL), _p("AMOUNT", ParagraphStyle("al", parent=LABEL, alignment=2))],
        [_p(payment.payer_name, BOLD), _p(format_money(payment.amount), ParagraphStyle("av", parent=RIGHT_BOLD, fontSize=15, leading=19))],
        [_p(f"{payment.method}" + (f" · Ref. {payment.reference}" if payment.reference else ""), SMALL), ""],
    ], colWidths=[FRAME_W * 0.6, FRAME_W * 0.4])
    amount_box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT), ("LEFTPADDING", (0, 0), (-1, -1), 7),
                                    ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, 0), 6),
                                    ("BOTTOMPADDING", (0, -1), (-1, -1), 6), ("VALIGN", (0, 0), (-1, -1), "TOP")]))

    story = [head, Spacer(1, 7 * mm), amount_box, Spacer(1, 6 * mm)]
    if allocations:
        rows = [[_p("For invoice", HEAD), _p("Applied", HEAD_R), _p("Still due", HEAD_R)]]
        for a in allocations:
            rows.append([_p(a.invoice.number, BASE), _p(format_money(a.amount, a.invoice.currency), RIGHT),
                         _p(format_money(a.invoice.get_balance(), a.invoice.currency), RIGHT)])
        table = Table(rows, colWidths=[FRAME_W * 0.4, FRAME_W * 0.3, FRAME_W * 0.3], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), EUCALYPTUS), ("LINEBELOW", (0, 1), (-1, -1), 0.5, LINE),
                                   ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story += [table, Spacer(1, 4 * mm)]
    credit = payment.get_unallocated()
    if credit > 0:
        story.append(_p(f"{format_money(credit)} is held as credit on your account.", BASE))
    if payment.is_reversed:
        story += [Spacer(1, 3 * mm), _p(f"This payment was reversed on {payment.reversed_at:%d %b %Y}: "
                                        f"{payment.reversal_reason}", ParagraphStyle("rv", parent=BASE, textColor=BRICK))]
    story += [Spacer(1, 6 * mm), _p("Thank you.", BASE)]
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    buffer.seek(0)
    return buffer
