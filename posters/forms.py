import calendar
from pathlib import Path

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import ImageFont

from core.forms import BootstrapFormMixin
from core.validators import validate_image_upload

from .models import FontAsset, Occasion, PosterTemplate, TemplateField
from .services import check_design_images

IMAGE_ACCEPT = "image/png,image/jpeg,image/webp"


class DesignUploadForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=100, label="Design name", help_text="e.g. “Hospital notice, square”.")
    background = forms.ImageField(
        label="Design picture", validators=[validate_image_upload],
        widget=forms.FileInput(attrs={"accept": IMAGE_ACCEPT}),
        help_text="PNG or JPG exported from Canva or similar, with empty spaces where the text will go. "
                  "1080×1080 for posts, 1080×1920 for WhatsApp Status.")
    overlay = forms.ImageField(
        required=False, label="Overlay (optional)", validators=[validate_image_upload],
        widget=forms.FileInput(attrs={"accept": "image/png"}),
        help_text="A transparent PNG of the same size, drawn above the logo (e.g. a frame). Most designs don't need one.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_bootstrap()

    def clean(self):
        data = super().clean()
        if data.get("background") and not self.errors:
            try:
                check_design_images(data["background"], data.get("overlay"))
            except ValidationError as exc:
                self.add_error("overlay" if "overlay" in exc.messages[0] else "background", exc)
        return data


class FontUploadForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=100, label="Font name", help_text="As you'll see it in the design editor, e.g. “Poppins Bold”.")
    file = forms.FileField(label="Font file", widget=forms.FileInput(attrs={"accept": ".ttf,.otf"}),
                           help_text="A .ttf or .otf file. Check that its licence lets you use it on posters.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_bootstrap()

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if FontAsset.objects.filter(name__iexact=name).exists():
            raise ValidationError("There is already a font with this name.")
        return name

    def clean_file(self):
        file = self.cleaned_data["file"]
        if Path(file.name).suffix.lower() not in (".ttf", ".otf"):
            raise ValidationError("Upload a .ttf or .otf font file.")
        if file.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise ValidationError(f"File is too large. The limit is {settings.MAX_UPLOAD_MB} MB.")
        try:
            file.seek(0)
            ImageFont.truetype(file, 24)
        except (OSError, ValueError):
            raise ValidationError("This file isn't a font the app can read.")
        finally:
            file.seek(0)
        return file


class OccasionForm(BootstrapFormMixin, forms.ModelForm):
    month = forms.TypedChoiceField(coerce=int, choices=[(m, calendar.month_name[m]) for m in range(1, 13)])

    class Meta:
        model = Occasion
        fields = ["name", "day", "month", "year", "headline", "message", "is_active"]
        labels = {"is_active": "Show in upcoming occasions"}
        widgets = {"message": forms.Textarea(attrs={"rows": 4}), "day": forms.NumberInput(attrs={"min": 1, "max": 31})}
        help_texts = {"message": "Start a line with a dash (-) for a bullet point."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_bootstrap()
        self.fields["is_active"].widget.attrs["class"] = "form-check-input"

    def clean(self):
        data = super().clean()
        month, day = data.get("month"), data.get("day")
        if month and day:
            longest = calendar.monthrange(data.get("year") or 2000, month)[1]
            if day > longest:
                self.add_error("day", f"{calendar.month_name[month]} has {longest} days.")
        return data


class PosterForm(BootstrapFormMixin, forms.Form):
    """The poster's name and design, plus one box per text box of the design."""

    name = forms.CharField(max_length=120, label="Poster name", help_text="Only for you, to find it again.")
    template = forms.ModelChoiceField(queryset=PosterTemplate.objects.none(), label="Design", empty_label=None)

    def __init__(self, *args, version, **kwargs):
        super().__init__(*args, **kwargs)
        self.version = version
        self.fields["template"].queryset = (PosterTemplate.objects.filter(is_active=True, versions__fields__isnull=False)
                                            .distinct().order_by("name"))
        self.text_fields = []
        for f in version.fields.all():
            if f.is_image:
                continue
            multiline = f.source == TemplateField.Source.MESSAGE or f.max_lines > 3
            name = f"v_{f.key}"
            self.fields[name] = forms.CharField(
                required=False, max_length=2000, label=f.label,
                widget=forms.Textarea(attrs={"rows": 4}) if multiline else forms.TextInput(),
                help_text="Start a line with a dash (-) for a bullet point." if multiline else "")
            self.text_fields.append(name)
        self._apply_bootstrap()
        for name in self.text_fields:
            self.fields[name].widget.attrs["data-poster-value"] = name[2:]

    @property
    def text_bound_fields(self):
        return [self[name] for name in self.text_fields]

    def values(self) -> dict[str, str]:
        return {name[2:]: self.cleaned_data.get(name, "") for name in self.text_fields}
