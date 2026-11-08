"""
Draws a poster with Pillow. The preview and the export both call
render_poster(), so what you see is exactly what you export.

The design is never altered: the background is drawn as it is, images are
fitted inside their boxes, the overlay goes on top, and text is drawn in its
box with the box's font. Text that is too long is made smaller (down to the
box's smallest size) and wrapped; if it still doesn't fit, it is cut short with
"…" and a warning is returned so the page can say which box is too full.
"""

from dataclasses import dataclass, field
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps

ELLIPSIS = "…"


@dataclass(frozen=True)
class Box:
    """What render_poster needs to know about one box (see TemplateField)."""

    key: str
    label: str
    x: int
    y: int
    w: int
    h: int
    is_image: bool = False
    font_path: str = ""
    size: int = 48
    min_size: int = 20
    color: str = "#1C2B28"
    align: str = "left"
    valign: str = "top"
    max_lines: int = 3
    line_spacing: float = 1.2
    uppercase: bool = False
    fit: str = "contain"


@dataclass
class RenderResult:
    image: Image.Image
    warnings: list[str] = field(default_factory=list)


@lru_cache(maxsize=256)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _prepare(text: str, uppercase: bool) -> list[str]:
    """Paragraphs to draw. A line starting with "- " or "* " becomes a bullet."""
    paragraphs = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line[:2] in ("- ", "* "):
            line = "• " + line[2:].strip()
        paragraphs.append(line.upper() if uppercase else line)
    while paragraphs and not paragraphs[-1]:
        paragraphs.pop()
    return paragraphs


def _break_word(word: str, font, width: int) -> list[str]:
    """A word wider than the box is split across lines."""
    pieces, current = [], ""
    for ch in word:
        if current and font.getlength(current + ch) > width:
            pieces.append(current)
            current = ch
        else:
            current += ch
    return pieces + ([current] if current else [])


def wrap(paragraphs: list[str], font, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in paragraphs:
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for word in paragraph.split(" "):
            if not word:
                continue
            candidate = f"{current} {word}" if current else word
            if font.getlength(candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            if font.getlength(word) <= width:
                current = word
            else:
                *full, current = _break_word(word, font, width)
                lines.extend(full)
        lines.append(current)
    return lines


@dataclass(frozen=True)
class TextLayout:
    font: ImageFont.FreeTypeFont
    lines: list[str]
    line_height: int
    fits: bool


def layout_text(text: str, box: Box) -> TextLayout:
    """Largest size from box.size down to box.min_size at which the text fits
    the box; at the smallest size, the lines that fit, with "…" if cut short."""
    paragraphs = _prepare(text, box.uppercase)
    size = max(box.size, box.min_size)
    while True:
        font = _font(box.font_path, size)
        line_height = max(1, round(size * box.line_spacing))
        lines = wrap(paragraphs, font, box.w)
        if len(lines) <= box.max_lines and len(lines) * line_height <= box.h + (line_height - size):
            return TextLayout(font, lines, line_height, True)
        if size <= box.min_size:
            break
        size = max(box.min_size, size - 2)
    room = max(1, min(box.max_lines, (box.h + line_height - size) // line_height))
    kept = lines[:room]
    last = kept[-1].rstrip() if kept else ""
    while last and font.getlength(last + ELLIPSIS) > box.w:
        last = last[:-1].rstrip()
    kept[-1:] = [last + ELLIPSIS]
    return TextLayout(font, kept, line_height, False)


def _draw_text(canvas: Image.Image, box: Box, text: str) -> bool:
    if not (text or "").strip():
        return True
    layout = layout_text(text, box)
    draw = ImageDraw.Draw(canvas)
    ascent, descent = layout.font.getmetrics()
    block = (len(layout.lines) - 1) * layout.line_height + ascent + descent
    if box.valign == "middle":
        top = box.y + (box.h - block) / 2
    elif box.valign == "bottom":
        top = box.y + box.h - block
    else:
        top = box.y
    for index, line in enumerate(layout.lines):
        baseline = top + ascent + index * layout.line_height
        width = layout.font.getlength(line)
        if box.align == "center":
            x = box.x + (box.w - width) / 2
        elif box.align == "right":
            x = box.x + box.w - width
        else:
            x = box.x
        draw.text((round(x), round(baseline)), line, font=layout.font, fill=box.color, anchor="ls")
    return layout.fits


def _paste_image(canvas: Image.Image, box: Box, picture: Image.Image) -> None:
    picture = ImageOps.exif_transpose(picture).convert("RGBA")
    if box.fit == "cover":
        fitted = ImageOps.fit(picture, (box.w, box.h), Image.Resampling.LANCZOS)
        canvas.alpha_composite(fitted, (box.x, box.y))
        return
    fitted = ImageOps.contain(picture, (box.w, box.h), Image.Resampling.LANCZOS)
    canvas.alpha_composite(fitted, (box.x + (box.w - fitted.width) // 2, box.y + (box.h - fitted.height) // 2))


def render_poster(background: Image.Image, boxes: list[Box], texts: dict[str, str],
                  images: dict[str, Image.Image] | None = None, overlay: Image.Image | None = None) -> RenderResult:
    """The finished poster at the design's full size, plus a warning for each
    box whose text had to be cut short."""
    images = images or {}
    canvas = background.convert("RGBA")
    for box in boxes:
        if box.is_image and box.key in images:
            _paste_image(canvas, box, images[box.key])
    if overlay is not None:
        canvas.alpha_composite(overlay.convert("RGBA").resize(canvas.size))
    warnings = []
    for box in boxes:
        if not box.is_image and box.font_path and not _draw_text(canvas, box, texts.get(box.key, "")):
            warnings.append(f"“{box.label}” is too long for its box and has been cut short.")
    return RenderResult(canvas.convert("RGB"), warnings)
