"""Upload validation and safe file naming."""

import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from PIL import Image, UnidentifiedImageError

ALLOWED_IMAGE_FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
ALLOWED_EXTENSIONS = set(ALLOWED_IMAGE_FORMATS.values())

phone_validator = RegexValidator(
    regex=r"^\+?[0-9 ()\-]{7,20}$",
    message="Enter a valid phone number (digits, spaces, +, - and brackets only).",
)


def validate_image_upload(file) -> None:
    """Reject files that are too large or are not genuinely PNG/JPEG/WebP images.

    The extension and browser-supplied content type are ignored: the bytes are
    opened with Pillow, so a renamed .exe or .svg is rejected.
    """
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if file.size and file.size > max_bytes:
        raise ValidationError(f"File is too large. The limit is {settings.MAX_UPLOAD_MB} MB.")
    try:
        file.seek(0)
        with Image.open(file) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise ValidationError("Upload a valid PNG, JPEG or WebP image.")
    finally:
        try:
            file.seek(0)
        except Exception:  # pragma: no cover - closed file
            pass
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError("Only PNG, JPEG or WebP images are allowed.")


def _random_name(folder: str, original_name: str) -> str:
    ext = Path(original_name).suffix.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ALLOWED_EXTENSIONS:
        ext = "img"
    return f"{folder}/{uuid.uuid4().hex}.{ext}"


def logo_upload_path(instance, filename: str) -> str:
    return _random_name("branding", filename)


def product_photo_path(instance, filename: str) -> str:
    return _random_name("products", filename)


def product_thumb_path(instance, filename: str) -> str:
    return _random_name("products/thumbs", filename)
