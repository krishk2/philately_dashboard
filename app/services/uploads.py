"""Validated image uploads.

Files are checked by actually decoding them with Pillow (not by trusting the extension),
then saved under a random name so users cannot overwrite each other's files.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from flask import current_app
from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from ..verification.imagehash import dhash
from .errors import ValidationError

FORMAT_EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


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
            image_hash = dhash(image)
            if fmt not in FORMAT_EXTENSIONS:
                raise ValidationError("Image must be a PNG, JPEG or WebP file.")
            folder = Path(current_app.config["UPLOAD_FOLDER"])
            folder.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid.uuid4().hex}.{FORMAT_EXTENSIONS[fmt]}"
            image.save(folder / filename, format=fmt)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError("The file is not a readable image.") from exc

    return filename, image_hash


def delete_image(filename: str) -> None:
    path = Path(current_app.config["UPLOAD_FOLDER"]) / filename
    path.unlink(missing_ok=True)
