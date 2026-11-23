"""Turns an uploaded key image into a one-page PDF, so storage, inspection and OCR stay PDF-only."""

import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from .pdf import PdfRejected

# A multi-picture JPEG from a phone camera decodes as MPO; only its first picture is used.
IMAGE_FORMATS: dict[str, tuple[str, ...]] = {"image/png": ("PNG",), "image/jpeg": ("JPEG", "MPO")}
MAX_PIXELS = 40_000_000
MAX_SIDE = 65_500  # the JPEG encoder inside the PDF cannot go higher
MAX_PAGE_POINTS = 14_400  # the PDF page size limit
JPEG_QUALITY = 90


def flatten(image: Image.Image) -> Image.Image:
    if image.mode.startswith("I"):
        # A direct conversion clips 16-bit values to white.
        image = image.convert("I").point(lambda value: value * (1 / 256)).convert("L")
    if image.has_transparency_data:
        rgba = image.convert("RGBA")
        page = Image.new("RGB", rgba.size, "white")
        page.paste(rgba, mask=rgba.getchannel("A"))
        return page
    return image.convert("RGB")


def decode(path: Path, media_type: str) -> Image.Image:
    formats = IMAGE_FORMATS[media_type]
    try:
        with Image.open(path, formats=formats) as image:
            width, height = image.size
            if image.format not in formats or width * height > MAX_PIXELS or max(width, height) > MAX_SIDE:
                raise PdfRejected("corrupt_image")
            image.load()
            page = flatten(ImageOps.exif_transpose(image))
    except PdfRejected:
        raise
    except Exception as err:  # Pillow raises many types on hostile data.
        raise PdfRejected("corrupt_image") from err
    page.info.clear()  # no EXIF, ICC or text chunks reach the stored file
    return page


def replace_with_pdf(path: Path, media_type: str) -> None:
    """Overwrites `path`, so the original image bytes are never stored or sent."""
    page = decode(path, media_type)
    handle, name = tempfile.mkstemp(suffix=".pdf", dir=path.parent)
    os.close(handle)
    out = Path(name)
    try:
        resolution = max(72.0, max(page.size) * 72 / MAX_PAGE_POINTS)
        page.save(out, "PDF", resolution=resolution, quality=JPEG_QUALITY)
        out.replace(path)
    finally:
        out.unlink(missing_ok=True)
