"""
The designs and calendar the app starts with, so posters can be made before
you have designs of your own.

* Two designs, each in square (1080×1080, posts) and story (1080×1920, WhatsApp
  Status): "Green band" and "Fresh card", in the app's green with a yellow
  accent. Their backgrounds are drawn here with Pillow; replace or add designs
  any time under Posters → Designs.
* A short list of well-known health days and Kenyan public holidays with fixed
  dates. Check them and add your own under Posters → Calendar; holidays whose
  date moves (e.g. Easter) are left for you to add with their year.
"""

import io
import math

from PIL import Image, ImageDraw, ImageFilter

GREEN = "#1F6F5C"
GREEN_DARK = "#174F42"
INK = "#1C2B28"
MINT = "#E3EFEB"
PAPER = "#F5FAF8"
YELLOW = "#F2C14E"
WHITE = "#FFFFFF"

SS = 2  # draw at twice the size, then shrink: smooth edges

BUNDLED_FONTS = {
    "regular": ("Montserrat Regular", "Montserrat_400Regular.ttf"),
    "semibold": ("Montserrat SemiBold", "Montserrat_600SemiBold.ttf"),
    "extrabold": ("Montserrat ExtraBold", "Montserrat_800ExtraBold.ttf"),
}

STARTER_OCCASIONS = [
    # (name, month, day)
    ("New Year's Day", 1, 1),
    ("World Cancer Day", 2, 4),
    ("World Water Day", 3, 22),
    ("World Tuberculosis Day", 3, 24),
    ("World Health Day", 4, 7),
    ("World Malaria Day", 4, 25),
    ("Labour Day", 5, 1),
    ("World Hand Hygiene Day", 5, 5),
    ("Madaraka Day", 6, 1),
    ("World Blood Donor Day", 6, 14),
    ("World Hepatitis Day", 7, 28),
    ("World Patient Safety Day", 9, 17),
    ("World Mental Health Day", 10, 10),
    ("Global Handwashing Day", 10, 15),
    ("Mashujaa Day", 10, 20),
    ("World Toilet Day", 11, 19),
    ("World AIDS Day", 12, 1),
    ("Jamhuri Day", 12, 12),
    ("Christmas Day", 12, 25),
]


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _bubbles(layer: Image.Image, spots, color) -> None:
    draw = ImageDraw.Draw(layer)
    for cx, cy, r in spots:
        draw.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS], fill=color)


def band_background(width: int, height: int, band: int, footer: int, pill: tuple[int, int, int, int]) -> bytes:
    """Green top band with a wavy edge, yellow date pill, dark green footer."""
    W, H = width * SS, height * SS
    image = Image.new("RGBA", (W, H), PAPER)
    draw = ImageDraw.Draw(image)
    wave = [(0, 0), (W, 0)]
    for i in range(0, 101):
        x = W - W * i / 100
        y = band * SS + math.sin(i / 100 * math.pi * 2) * 34 * SS
        wave.append((x, y))
    draw.polygon(wave, fill=GREEN)
    decor = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _bubbles(decor, [(width - 120, 120, 150), (width - 300, band - 120, 60), (width - 60, band - 40, 34),
                     (width * 0.55, 70, 28)], (255, 255, 255, 34))
    _bubbles(decor, [(width - 140, height - footer - 130, 90), (width - 40, height - footer - 260, 40)],
             (31, 111, 92, 26))
    image.alpha_composite(decor)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, (height - footer) * SS, W, H], fill=GREEN_DARK)
    draw.rectangle([0, (height - footer) * SS, W, (height - footer + 6) * SS], fill=YELLOW)
    x, y, w, h = pill
    draw.rounded_rectangle([x * SS, y * SS, (x + w) * SS, (y + h) * SS], radius=h // 2 * SS, fill=YELLOW)
    return _png(image.resize((width, height), Image.Resampling.LANCZOS))


def card_background(width: int, height: int, pill: tuple[int, int, int, int], divider_y: int) -> bytes:
    """Soft mint background with bubbles and a white card; green date pill."""
    W, H = width * SS, height * SS
    top, bottom = (220, 238, 231), (191, 221, 210)
    gradient = Image.new("RGBA", (1, H))
    for y in range(H):
        t = y / (H - 1)
        gradient.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    image = gradient.resize((W, H))
    decor = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _bubbles(decor, [(80, 90, 110), (width - 60, height * 0.35, 140), (40, height - 140, 90),
                     (width - 150, height - 40, 70)], (255, 255, 255, 90))
    image.alpha_composite(decor)
    margin, radius = 64, 48
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [margin * SS, (margin + 14) * SS, W - margin * SS, H - (margin - 14) * SS], radius=radius * SS,
        fill=(23, 79, 66, 70))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18 * SS)))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([margin * SS, margin * SS, W - margin * SS, H - margin * SS], radius=radius * SS, fill=WHITE)
    draw.rounded_rectangle([(width / 2 - 60) * SS, (margin + 40) * SS, (width / 2 + 60) * SS, (margin + 50) * SS],
                           radius=5 * SS, fill=YELLOW)
    x, y, w, h = pill
    draw.rounded_rectangle([x * SS, y * SS, (x + w) * SS, (y + h) * SS], radius=h // 2 * SS, fill=GREEN)
    draw.rounded_rectangle([(width / 2 - 60) * SS, divider_y * SS, (width / 2 + 60) * SS, (divider_y + 6) * SS],
                           radius=3 * SS, fill=MINT)
    return _png(image.resize((width, height), Image.Resampling.LANCZOS))


def _field(key, source, label, box, font, size, min_size, color, align="left", valign="top", max_lines=1,
           line_spacing="1.2", uppercase=False):
    x, y, w, h = box
    return {"key": key, "source": source, "label": label, "x": x, "y": y, "w": w, "h": h, "font": font,
            "size": size, "min_size": min_size, "color": color, "align": align, "valign": valign,
            "max_lines": max_lines, "line_spacing": line_spacing, "uppercase": uppercase}


def _logo(box, fit="contain"):
    x, y, w, h = box
    return {"key": "logo", "source": "logo", "label": "Logo", "x": x, "y": y, "w": w, "h": h, "fit": fit}


def starter_designs() -> list[dict]:
    """Each design: name, background PNG bytes and its boxes (font = a BUNDLED_FONTS key)."""
    square_band_pill, story_band_pill = (80, 80, 380, 84), (80, 140, 440, 96)
    square_card_pill, story_card_pill = (340, 150, 400, 76), (320, 170, 440, 90)
    return [
        {
            "name": "Green band (square)",
            "background": band_background(1080, 1080, band=560, footer=140, pill=square_band_pill),
            "fields": [
                _field("date", "date", "Date", (100, 80, 340, 84), "extrabold", 34, 20, INK, "center", "middle",
                       uppercase=True),
                _field("headline", "headline", "Headline", (80, 196, 920, 300), "extrabold", 86, 44, WHITE,
                       max_lines=3, line_spacing="1.1"),
                _field("message", "message", "Message", (80, 610, 920, 290), "regular", 42, 26, INK,
                       max_lines=7, line_spacing="1.3"),
                _logo((70, 958, 104, 104)),
                _field("business_name", "business_name", "Business name", (200, 962, 820, 50), "semibold", 38, 24,
                       WHITE, valign="middle"),
                _field("contacts", "contacts", "Contacts", (200, 1014, 820, 44), "regular", 28, 18, WHITE,
                       valign="middle"),
            ],
        },
        {
            "name": "Green band (story)",
            "background": band_background(1080, 1920, band=980, footer=200, pill=story_band_pill),
            "fields": [
                _field("date", "date", "Date", (100, 140, 400, 96), "extrabold", 40, 22, INK, "center", "middle",
                       uppercase=True),
                _field("headline", "headline", "Headline", (80, 300, 920, 560), "extrabold", 112, 56, WHITE,
                       max_lines=4, line_spacing="1.08"),
                _field("message", "message", "Message", (80, 1060, 920, 560), "regular", 52, 30, INK,
                       max_lines=9, line_spacing="1.3"),
                _logo((70, 1745, 150, 150)),
                _field("business_name", "business_name", "Business name", (250, 1760, 780, 60), "semibold", 46, 26,
                       WHITE, valign="middle"),
                _field("contacts", "contacts", "Contacts", (250, 1830, 780, 50), "regular", 34, 20, WHITE,
                       valign="middle"),
            ],
        },
        {
            "name": "Fresh card (square)",
            "background": card_background(1080, 1080, pill=square_card_pill, divider_y=540),
            "fields": [
                _field("date", "date", "Date", (360, 150, 360, 76), "extrabold", 30, 18, WHITE, "center", "middle",
                       uppercase=True),
                _field("headline", "headline", "Headline", (130, 250, 820, 270), "extrabold", 78, 40, GREEN,
                       "center", "middle", max_lines=3, line_spacing="1.1"),
                _field("message", "message", "Message", (130, 572, 820, 230), "regular", 38, 24, INK, "center",
                       "middle", max_lines=6, line_spacing="1.3"),
                _logo((480, 818, 120, 72)),
                _field("business_name", "business_name", "Business name", (130, 898, 820, 46), "semibold", 34, 22,
                       GREEN, "center", "middle"),
                _field("contacts", "contacts", "Contacts", (130, 946, 820, 40), "regular", 26, 18, INK, "center",
                       "middle"),
            ],
        },
        {
            "name": "Fresh card (story)",
            "background": card_background(1080, 1920, pill=story_card_pill, divider_y=900),
            "fields": [
                _field("date", "date", "Date", (340, 170, 400, 90), "extrabold", 36, 20, WHITE, "center", "middle",
                       uppercase=True),
                _field("headline", "headline", "Headline", (130, 320, 820, 540), "extrabold", 104, 52, GREEN,
                       "center", "middle", max_lines=4, line_spacing="1.08"),
                _field("message", "message", "Message", (130, 950, 820, 520), "regular", 48, 28, INK, "center",
                       "middle", max_lines=9, line_spacing="1.3"),
                _logo((450, 1520, 180, 120)),
                _field("business_name", "business_name", "Business name", (130, 1668, 820, 60), "semibold", 44, 26,
                       GREEN, "center", "middle"),
                _field("contacts", "contacts", "Contacts", (130, 1738, 820, 50), "regular", 32, 20, INK, "center",
                       "middle"),
            ],
        },
    ]
