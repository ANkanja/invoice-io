"""
Posters: designs, posters, exports and the calendar. Views call these; they
never change designs, lock versions or write exports themselves.
"""

import datetime
import io
import json
import re

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from django.utils.text import slugify
from PIL import Image

from core.models import AuditLog, BusinessSettings

from . import starters
from .models import FontAsset, Occasion, Poster, PosterExport, PosterTemplate, TemplateField, TemplateVersion
from .rendering import Box, RenderResult, render_poster

Source = TemplateField.Source
MAX_FIELDS = 30
MIN_SIDE, MAX_SIDE = 300, 4000
TEXT_SOURCES_FROM_SETTINGS = {Source.BUSINESS_NAME, Source.PHONE, Source.CONTACTS}


# --- Fonts -----------------------------------------------------------------------------------

def ensure_bundled_fonts() -> dict[str, FontAsset]:
    """The free fonts that come with the app, keyed "regular", "semibold", "extrabold"."""
    fonts = {}
    for key, (name, filename) in starters.BUNDLED_FONTS.items():
        font = FontAsset.objects.filter(name=name).first()
        if font is None:
            font = FontAsset.objects.create(name=name, bundled_file=filename)
        fonts[key] = font
    return fonts


@transaction.atomic
def add_font(name: str, file, user) -> FontAsset:
    font = FontAsset.objects.create(name=" ".join(name.split()), file=file)
    AuditLog.record(actor=user, action="poster.font_added", obj=font)
    return font


# --- Designs ---------------------------------------------------------------------------------

def image_size(file) -> tuple[int, int]:
    file.seek(0)
    with Image.open(file) as picture:
        size = picture.size
    file.seek(0)
    return size


def check_design_images(background, overlay=None) -> tuple[int, int]:
    """(width, height) of the background; refuses sizes the app can't work with
    and an overlay that isn't exactly the background's size."""
    width, height = image_size(background)
    if min(width, height) < MIN_SIDE or max(width, height) > MAX_SIDE:
        raise ValidationError(f"The design must be between {MIN_SIDE} and {MAX_SIDE} pixels on each side "
                              f"(this one is {width}×{height}).")
    if overlay and image_size(overlay) != (width, height):
        raise ValidationError(f"The overlay must be exactly the same size as the design ({width}×{height}).")
    return width, height


@transaction.atomic
def create_design(*, name: str, background, overlay=None, user) -> PosterTemplate:
    """A design from your own picture (e.g. a Canva export). It starts with no
    boxes: mark where the text and logo go in the design editor."""
    width, height = check_design_images(background, overlay)
    template = PosterTemplate.objects.create(name=" ".join(name.split()))
    version = TemplateVersion(template=template, version_no=1, width=width, height=height, created_by=user)
    version.background.save(background.name, background, save=False)
    if overlay:
        version.overlay.save(overlay.name, overlay, save=False)
    version.save()
    AuditLog.record(actor=user, action="poster.design_created", obj=template, changes={"size": f"{width}×{height}"})
    return template


@transaction.atomic
def create_starter_designs(user) -> list[PosterTemplate]:
    """Add the designs that come with the app (once; later calls add nothing)."""
    if PosterTemplate.objects.filter(is_starter=True).exists():
        return []
    fonts = ensure_bundled_fonts()
    created = []
    for design in starters.starter_designs():
        template = PosterTemplate.objects.create(name=design["name"], is_starter=True)
        picture = Image.open(io.BytesIO(design["background"]))
        version = TemplateVersion(template=template, version_no=1, width=picture.width, height=picture.height,
                                  created_by=user)
        version.background.save(f"{slugify(design['name'])}.png", ContentFile(design["background"]), save=False)
        version.save()
        for position, data in enumerate(design["fields"]):
            data = dict(data)
            font_key = data.pop("font", None)
            TemplateField.objects.create(version=version, position=position,
                                         font=fonts[font_key] if font_key else None, **data)
        created.append(template)
    AuditLog.record(actor=user, action="poster.starter_designs_added", obj=created[0],
                    changes={"designs": [t.name for t in created]})
    return created


def parse_fields(raw: str, version: TemplateVersion) -> list[dict]:
    """Check the boxes sent by the design editor (JSON) and return clean values.
    Every box must sit inside the design; text boxes need a font."""
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        raise ValidationError("The boxes could not be read. Reload the page and try again.")
    if not isinstance(items, list):
        raise ValidationError("The boxes could not be read. Reload the page and try again.")
    if len(items) > MAX_FIELDS:
        raise ValidationError(f"A design can have at most {MAX_FIELDS} boxes.")
    fonts = {f.pk: f for f in FontAsset.objects.all()}
    choices = set(Source.values)
    clean, keys = [], set()
    for number, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"Box {number} could not be read.")
        source = item.get("source")
        if source not in choices:
            raise ValidationError(f"Box {number}: choose what goes in it.")
        label = " ".join(str(item.get("label") or Source(source).label).split())[:60]
        key = str(item.get("key") or source)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key):
            raise ValidationError(f"“{label}”: the box name can only use small letters, digits and _.")
        if key in keys:
            raise ValidationError(f"Two boxes are called “{key}”. Each box needs its own name.")
        keys.add(key)

        def number_of(name, low, high, default=None):
            value = item.get(name, default)
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValidationError(f"“{label}”: {name.replace('_', ' ')} must be a number.")
            if not low <= value <= high:
                raise ValidationError(f"“{label}”: {name.replace('_', ' ')} must be between {low} and {high}.")
            return value

        x = int(number_of("x", 0, version.width - 10))
        y = int(number_of("y", 0, version.height - 10))
        w = int(number_of("w", 10, version.width - x))
        h = int(number_of("h", 10, version.height - y))
        values = {"key": key, "source": source, "label": label, "x": x, "y": y, "w": w, "h": h, "position": number}
        if source in TemplateField.IMAGE_SOURCES:
            fit = item.get("fit") or TemplateField.Fit.CONTAIN
            if fit not in TemplateField.Fit.values:
                raise ValidationError(f"“{label}”: choose how the picture fits.")
            values["fit"] = fit
        else:
            try:
                font = fonts.get(int(item.get("font")))
            except (TypeError, ValueError):
                font = None
            if font is None:
                raise ValidationError(f"“{label}”: choose a font.")
            size = int(number_of("size", 6, 400))
            min_size = int(number_of("min_size", 6, size, item.get("min_size") or size))
            color = str(item.get("color") or "")
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                raise ValidationError(f"“{label}”: choose a colour.")
            align, valign = item.get("align") or "left", item.get("valign") or "top"
            if align not in TemplateField.Align.values or valign not in TemplateField.VAlign.values:
                raise ValidationError(f"“{label}”: choose how the text lines up.")
            values.update(font=font, size=size, min_size=min_size, color=color.upper(), align=align, valign=valign,
                          max_lines=int(number_of("max_lines", 1, 40, item.get("max_lines") or 1)),
                          line_spacing=round(number_of("line_spacing", 0.8, 2.5, item.get("line_spacing") or 1.2), 2),
                          uppercase=bool(item.get("uppercase")))
        clean.append(values)
    return clean


@transaction.atomic
def save_design_fields(template: PosterTemplate, fields: list[dict], user) -> TemplateVersion:
    """Replace the boxes of the design's current version. If posters were
    already exported from it, a new version is made instead (same pictures),
    so earlier exports stay exactly as they were."""
    current = TemplateVersion.objects.select_for_update().filter(template=template).order_by("-version_no").first()
    if current.is_locked:
        version = TemplateVersion.objects.create(
            template=template, version_no=current.version_no + 1, background=current.background.name,
            overlay=current.overlay.name, width=current.width, height=current.height, created_by=user)
    else:
        version = current
        version.fields.all().delete()
    for data in fields:
        TemplateField.objects.create(version=version, **data)
    AuditLog.record(actor=user, action="poster.design_saved", obj=template,
                    changes={"version": version.version_no, "boxes": len(fields)})
    return version


@transaction.atomic
def set_design_active(template: PosterTemplate, active: bool, user) -> PosterTemplate:
    if template.is_active != active:
        template.is_active = active
        template.save(update_fields=["is_active", "updated_at"])
        AuditLog.record(actor=user, action="poster.design_restored" if active else "poster.design_archived",
                        obj=template)
    return template


# --- Filling and drawing --------------------------------------------------------------------

def boxes_for(version: TemplateVersion) -> list[Box]:
    return [
        Box(key=f.key, label=f.label, x=f.x, y=f.y, w=f.w, h=f.h, is_image=f.is_image,
            font_path=f.font.path if f.font_id else "", size=f.size, min_size=f.min_size, color=f.color,
            align=f.align, valign=f.valign, max_lines=f.max_lines, line_spacing=float(f.line_spacing),
            uppercase=f.uppercase, fit=f.fit)
        for f in version.fields.select_related("font")
    ]


def settings_text(source: str, settings: BusinessSettings) -> str:
    if source == Source.BUSINESS_NAME:
        return settings.name
    if source == Source.PHONE:
        return settings.phone
    if source == Source.CONTACTS:
        return "  ·  ".join(part for part in (settings.phone, settings.email) if part)
    return ""


def default_values(version: TemplateVersion | None, occasion: Occasion | None = None) -> dict[str, str]:
    """What a new poster starts with: the occasion's headline, message and date,
    and your business name and contacts from Settings."""
    settings = BusinessSettings.load()
    values = {}
    for f in version.fields.all() if version else []:
        if f.is_image:
            continue
        if f.source in TEXT_SOURCES_FROM_SETTINGS:
            values[f.key] = settings_text(f.source, settings)
        elif occasion and f.source == Source.HEADLINE:
            values[f.key] = occasion.headline or occasion.name
        elif occasion and f.source == Source.MESSAGE:
            values[f.key] = occasion.message
        elif occasion and f.source == Source.DATE:
            values[f.key] = occasion.date_text()
        else:
            values[f.key] = ""
    return values


SAMPLE_TEXT = {
    Source.HEADLINE: "Your headline here",
    Source.MESSAGE: "Your message goes here.\n- Start a line with a dash for a bullet point\n- Text shrinks to fit its box",
    Source.DATE: "15 October",
    Source.CUSTOM: "Extra text",
}


def sample_values(version: TemplateVersion) -> dict[str, str]:
    """Placeholder text for previews of a design (business details from Settings)."""
    settings = BusinessSettings.load()
    values = {}
    for f in version.fields.all():
        if f.source in TEXT_SOURCES_FROM_SETTINGS:
            values[f.key] = settings_text(f.source, settings) or Source(f.source).label
        else:
            values[f.key] = SAMPLE_TEXT.get(f.source, "")
    return values


def _open(field_file) -> Image.Image:
    with field_file.open("rb") as fh:
        picture = Image.open(fh)
        picture.load()
    return picture


def render_version(version: TemplateVersion, values: dict[str, str]) -> RenderResult:
    """The poster for this design version and these texts, at full size."""
    images = {}
    settings = BusinessSettings.load()
    if settings.logo:
        try:
            logo = _open(settings.logo)
        except (OSError, ValueError):
            logo = None
        if logo is not None:
            images = {f.key: logo for f in version.fields.all() if f.source == Source.LOGO}
    overlay = _open(version.overlay) if version.overlay else None
    texts = {key: str(value) for key, value in values.items()}
    return render_poster(_open(version.background), boxes_for(version), texts, images, overlay)


def to_bytes(image: Image.Image, *, max_width: int | None = None, fmt: str = "PNG") -> bytes:
    if max_width and image.width > max_width:
        image = image.resize((max_width, round(image.height * max_width / image.width)), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    if fmt == "JPEG":
        image.save(buffer, "JPEG", quality=85)
    else:
        image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


# --- Posters and exports --------------------------------------------------------------------

def clean_values(version: TemplateVersion, submitted: dict) -> dict[str, str]:
    """Only the text boxes of this design, as trimmed text (at most 2,000 characters each)."""
    return {f.key: str(submitted.get(f.key, "")).strip()[:2000] for f in version.fields.all() if not f.is_image}


@transaction.atomic
def create_poster(*, template: PosterTemplate, user, occasion: Occasion | None = None, name: str = "") -> Poster:
    version = template.current_version
    if version is None or not version.fields.exists():
        raise ValidationError("This design has no boxes yet. Mark where the text goes in the design editor first.")
    poster = Poster.objects.create(
        name=name or (f"{occasion.name} {datetime.date.today().year}" if occasion else template.name),
        template=template, occasion=occasion, values=default_values(version, occasion), created_by=user)
    AuditLog.record(actor=user, action="poster.created", obj=poster)
    return poster


@transaction.atomic
def save_poster(poster: Poster, *, name: str, template: PosterTemplate, values: dict, user) -> Poster:
    version = template.current_version
    poster.name = " ".join(name.split()) or poster.name
    # Switching designs keeps what you typed in boxes both designs have.
    poster.values = {**poster.values, **clean_values(version, values)}
    poster.template = template
    poster.save()
    return poster


@transaction.atomic
def export_poster(poster: Poster, user) -> PosterExport:
    """A new PNG of the poster at the design's full size, kept for good. The
    design version it used is locked so it can't change under the export."""
    poster = Poster.objects.select_for_update().get(pk=poster.pk)
    version = TemplateVersion.objects.select_for_update().get(pk=poster.template.current_version.pk)
    values = clean_values(version, poster.values)
    result = render_version(version, values)
    if not version.is_locked:
        version.is_locked = True
        version.save(update_fields=["is_locked", "updated_at"])
    number = (poster.exports.aggregate(m=Max("export_no"))["m"] or 0) + 1
    export = PosterExport(poster=poster, export_no=number, version=version, width=result.image.width,
                          height=result.image.height, values=values, created_by=user)
    export.file.save(export_filename(poster, number), ContentFile(to_bytes(result.image)), save=False)
    export.save()
    AuditLog.record(actor=user, action="poster.exported", obj=poster, changes={"export": number})
    return export


def export_filename(poster: Poster, number: int) -> str:
    return f"{slugify(poster.name)[:50] or 'poster'}-{number}.png"


# --- Calendar --------------------------------------------------------------------------------

@transaction.atomic
def add_starter_occasions(user) -> int:
    """Add the well-known health days and holidays that aren't in the calendar yet."""
    existing = {name.lower() for name in Occasion.objects.values_list("name", flat=True)}
    added = 0
    for name, month, day in starters.STARTER_OCCASIONS:
        if name.lower() not in existing:
            Occasion.objects.create(name=name, month=month, day=day, headline=name)
            added += 1
    if added:
        AuditLog.record(actor=user, action="poster.calendar_filled", obj=BusinessSettings.load(),
                        changes={"occasions_added": added})
    return added


def upcoming_occasions(today: datetime.date, days: int = 30) -> list[tuple[Occasion, datetime.date, int]]:
    """Active occasions in the next `days` days: (occasion, date, days to go), soonest first."""
    soon = []
    for occasion in Occasion.objects.filter(is_active=True):
        when = occasion.next_date(today)
        if when is not None and (when - today).days <= days:
            soon.append((occasion, when, (when - today).days))
    return sorted(soon, key=lambda item: (item[1], item[0].name))
