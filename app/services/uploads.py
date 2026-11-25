"""Validated image uploads.

Files are checked by actually decoding them with Pillow (not by trusting the extension), then
re-encoded under a random name, so users cannot overwrite each other's files. Re-encoding also
drops embedded metadata such as the GPS location phones add to photos. Very large photos are
scaled down. The bytes go to whichever storage backend is configured (local disk or S3).
"""

from __future__ import annotations

import io
import uuid

from flask import current_app
from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from ..verification.imagehash import dhash
from .errors import ValidationError
from .storage import get_storage

FORMAT_EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MAX_DIMENSION = 2400  # px; plenty for a stamp photo


def save_image(file: FileStorage | None) -> tuple[str, str]:
    """Save an uploaded image. Returns (stored filename, dHash)."""
    if file is None or not file.filename:
        raise ValidationError("An image is required.")

    extension = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if extension not in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]:
        raise ValidationError("Image must be a PNG, JPEG or WebP file.")

    try:
        with Image.open(file.stream) as image:
            image.load()
            fmt = image.format
            if fmt not in FORMAT_EXTENSIONS:
                raise ValidationError("Image must be a PNG, JPEG or WebP file.")
            image_hash = dhash(image)
            if max(image.size) > MAX_DIMENSION:
                image.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
            if fmt == "JPEG" and image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format=fmt)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValidationError("The file is not a readable image.") from exc

    filename = f"{uuid.uuid4().hex}.{FORMAT_EXTENSIONS[fmt]}"
    get_storage().save(filename, buffer.getvalue(), CONTENT_TYPES[fmt])
    return filename, image_hash


def read_image(filename: str) -> bytes:
    return get_storage().read(filename)


def delete_image(filename: str) -> None:
    get_storage().delete(filename)
