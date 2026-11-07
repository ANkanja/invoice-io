"""
Product photo processing (Pillow), run once when a photo is uploaded.

* Turns phone photos the right way up (EXIF orientation).
* Removes all metadata — phone photos can carry GPS location.
* Limits the stored photo to 1600 px on the longest side (plenty for posters
  and print, a fraction of a raw phone photo's size).
* Keeps transparency: images with an alpha channel are stored as PNG (useful
  for cut-out product shots on posters); everything else as high-quality JPEG.
* Makes a 320 px WebP thumbnail for lists, so pages load fast on mobile data.
"""

from dataclasses import dataclass
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

MAX_SIDE = 1600
THUMB_SIDE = 320


@dataclass
class ProcessedPhoto:
    photo: ContentFile
    thumbnail: ContentFile
    width: int
    height: int


def _has_alpha(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA", "PA"):
        return True
    return img.mode == "P" and "transparency" in img.info


def process_product_photo(upload) -> ProcessedPhoto:
    upload.seek(0)
    with Image.open(upload) as original:
        icc = original.info.get("icc_profile")
        img = ImageOps.exif_transpose(original)
        alpha = _has_alpha(img)
        img = img.convert("RGBA" if alpha else "RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)

        main = BytesIO()
        extra = {"icc_profile": icc} if icc else {}
        if alpha:
            img.save(main, "PNG", optimize=True, **extra)
            ext = "png"
        else:
            img.save(main, "JPEG", quality=88, optimize=True, progressive=True, **extra)
            ext = "jpg"

        thumb_img = img.copy()
        thumb_img.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
        thumb = BytesIO()
        thumb_img.save(thumb, "WEBP", quality=80, method=4)

        return ProcessedPhoto(
            photo=ContentFile(main.getvalue(), name=f"photo.{ext}"),
            thumbnail=ContentFile(thumb.getvalue(), name="thumb.webp"),
            width=img.width,
            height=img.height,
        )
