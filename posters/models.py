"""
Posters: designs (templates) with marked boxes, the posters made from them,
the exported images, and a calendar of occasions to make posters for.

* A design never changes once something has been exported from it: each
  PosterTemplate has numbered TemplateVersions, a version locks on its first
  export, and editing a locked design creates the next version.
* Every export is a new PosterExport with its own file and a copy of the text
  used, so an old export can always be found exactly as it was sent.
"""

import calendar
import datetime
import uuid
from pathlib import Path

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q

from core.models import ImmutableRecordError, TimeStampedModel
from core.validators import validate_image_upload

BUNDLED_FONTS_DIR = Path(__file__).resolve().parent / "fonts"


def _upload(folder: str, filename: str, allowed: set[str], fallback: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    return f"{folder}/{uuid.uuid4().hex}.{ext if ext in allowed else fallback}"


def font_upload_path(instance, filename: str) -> str:
    return _upload("posters/fonts", filename, {"ttf", "otf"}, "ttf")


def design_upload_path(instance, filename: str) -> str:
    return _upload("posters/designs", filename, {"png", "jpg", "webp"}, "png")


def export_upload_path(instance, filename: str) -> str:
    return _upload("posters/exports", filename, {"png"}, "png")


class FontAsset(TimeStampedModel):
    """A font posters can be drawn in: one of the free fonts shipped with the
    app (posters/fonts, SIL Open Font Licence) or a .ttf/.otf you uploaded."""

    name = models.CharField(max_length=100, unique=True)
    file = models.FileField(upload_to=font_upload_path, blank=True)
    bundled_file = models.CharField(max_length=100, blank=True, editable=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(condition=~Q(file="") | ~Q(bundled_file=""), name="font_has_a_file"),
        ]

    def __str__(self):
        return self.name

    @property
    def path(self) -> str:
        if self.bundled_file:
            return str(BUNDLED_FONTS_DIR / self.bundled_file)
        return self.file.path

    @property
    def is_bundled(self) -> bool:
        return bool(self.bundled_file)


class PosterTemplate(TimeStampedModel):
    """A poster design. Its look lives in its versions."""

    name = models.CharField(max_length=100)
    is_starter = models.BooleanField(default=False, help_text="One of the designs that come with the app.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def current_version(self) -> "TemplateVersion | None":
        return self.versions.order_by("-version_no").first()


class TemplateVersion(TimeStampedModel):
    """One version of a design: a fixed-size background picture, an optional
    transparent overlay drawn above it, and the boxes to fill. Width and height
    are read from the picture, never typed."""

    template = models.ForeignKey(PosterTemplate, on_delete=models.PROTECT, related_name="versions")
    version_no = models.PositiveIntegerField()
    background = models.ImageField(upload_to=design_upload_path, validators=[validate_image_upload])
    overlay = models.ImageField(upload_to=design_upload_path, blank=True, validators=[validate_image_upload])
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    is_locked = models.BooleanField(default=False, help_text="Locked once a poster has been exported from it.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["template", "-version_no"]
        constraints = [
            models.UniqueConstraint(fields=["template", "version_no"], name="template_version_unique"),
            models.CheckConstraint(condition=Q(width__gt=0) & Q(height__gt=0), name="template_version_size_positive"),
        ]

    def __str__(self):
        return f"{self.template.name} (version {self.version_no})"

    @property
    def size_label(self) -> str:
        if self.width == self.height:
            shape = "Square"
        elif self.height > self.width:
            shape = "Story" if abs(self.height / self.width - 16 / 9) < 0.05 else "Portrait"
        else:
            shape = "Landscape"
        return f"{shape} {self.width}×{self.height}"


class TemplateField(models.Model):
    """A box on a design and what fills it. Positions are pixels on the
    original picture. Boxes are drawn in `position` order."""

    class Source(models.TextChoices):
        HEADLINE = "headline", "Headline"
        MESSAGE = "message", "Message"
        DATE = "date", "Date or occasion badge"
        BUSINESS_NAME = "business_name", "Business name"
        PHONE = "phone", "Phone"
        CONTACTS = "contacts", "Contacts (phone and email)"
        LOGO = "logo", "Logo"
        CUSTOM = "custom", "Extra text"

    class Align(models.TextChoices):
        LEFT = "left", "Left"
        CENTER = "center", "Centre"
        RIGHT = "right", "Right"

    class VAlign(models.TextChoices):
        TOP = "top", "Top"
        MIDDLE = "middle", "Middle"
        BOTTOM = "bottom", "Bottom"

    class Fit(models.TextChoices):
        CONTAIN = "contain", "Fit inside the box"
        COVER = "cover", "Fill the box (may crop)"

    IMAGE_SOURCES = frozenset({Source.LOGO})

    version = models.ForeignKey(TemplateVersion, on_delete=models.CASCADE, related_name="fields")
    position = models.PositiveSmallIntegerField(default=0)
    key = models.CharField(max_length=40, validators=[RegexValidator(r"^[a-z][a-z0-9_]{0,39}$")])
    source = models.CharField(max_length=20, choices=Source.choices)
    label = models.CharField(max_length=60)
    x = models.PositiveIntegerField()
    y = models.PositiveIntegerField()
    w = models.PositiveIntegerField(validators=[MinValueValidator(10)])
    h = models.PositiveIntegerField(validators=[MinValueValidator(10)])
    # Text boxes
    font = models.ForeignKey(FontAsset, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    size = models.PositiveSmallIntegerField(default=48, validators=[MinValueValidator(6), MaxValueValidator(400)])
    min_size = models.PositiveSmallIntegerField(default=20, validators=[MinValueValidator(6), MaxValueValidator(400)])
    color = models.CharField(max_length=7, default="#1C2B28", validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$")])
    align = models.CharField(max_length=6, choices=Align.choices, default=Align.LEFT)
    valign = models.CharField(max_length=6, choices=VAlign.choices, default=VAlign.TOP)
    max_lines = models.PositiveSmallIntegerField(default=3, validators=[MinValueValidator(1), MaxValueValidator(40)])
    line_spacing = models.DecimalField(max_digits=3, decimal_places=2, default=1.2,
                                       validators=[MinValueValidator(0.8), MaxValueValidator(2.5)])
    uppercase = models.BooleanField(default=False)
    # Image boxes
    fit = models.CharField(max_length=7, choices=Fit.choices, default=Fit.CONTAIN)

    class Meta:
        ordering = ["version", "position", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["version", "key"], name="template_field_key_unique"),
            models.CheckConstraint(condition=Q(min_size__lte=models.F("size")), name="template_field_min_size_lte_size"),
        ]

    def __str__(self):
        return f"{self.label} ({self.key})"

    @property
    def is_image(self) -> bool:
        return self.source in self.IMAGE_SOURCES


class Occasion(TimeStampedModel):
    """A date to make a poster for: a health day, a holiday or your own event.
    Repeats every year on the same day unless a year is given."""

    name = models.CharField(max_length=120)
    month = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    day = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(31)])
    year = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(2000), MaxValueValidator(2100)],
        help_text="Only for dates that move each year (e.g. Easter). Leave blank for the same day every year.",
    )
    headline = models.CharField(max_length=150, blank=True, help_text="Suggested poster headline.")
    message = models.TextField(blank=True, help_text="Suggested poster message.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["month", "day", "name"]

    def __str__(self):
        return self.name

    def next_date(self, today: datetime.date) -> datetime.date | None:
        """The next time this occasion falls on or after today (29 Feb → 28 Feb in other years)."""
        def on(year: int) -> datetime.date:
            return datetime.date(year, self.month, min(self.day, calendar.monthrange(year, self.month)[1]))

        if self.year:
            when = on(self.year)
            return when if when >= today else None
        when = on(today.year)
        return when if when >= today else on(today.year + 1)

    def date_text(self) -> str:
        """'15 October', as printed on a poster's date badge."""
        year = self.year or 2000  # a leap year, so 29 February reads correctly
        day = min(self.day, calendar.monthrange(year, self.month)[1])
        return f"{day} {datetime.date(year, self.month, day):%B}"


class Poster(TimeStampedModel):
    """A poster you are making: the design it uses and the text you typed."""

    name = models.CharField(max_length=120)
    template = models.ForeignKey(PosterTemplate, on_delete=models.PROTECT, related_name="posters")
    occasion = models.ForeignKey(Occasion, on_delete=models.SET_NULL, null=True, blank=True, related_name="posters")
    values = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-updated_at", "-pk"]

    def __str__(self):
        return self.name


class PosterExport(models.Model):
    """One exported image. Kept for good: never edited, never overwritten."""

    poster = models.ForeignKey(Poster, on_delete=models.PROTECT, related_name="exports")
    export_no = models.PositiveIntegerField()
    version = models.ForeignKey(TemplateVersion, on_delete=models.PROTECT, related_name="exports")
    file = models.ImageField(upload_to=export_upload_path)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    values = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["poster", "export_no"], name="poster_export_no_unique")]

    def __str__(self):
        return f"{self.poster.name} #{self.export_no}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ImmutableRecordError("Poster exports cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Poster exports are kept for your records.")
