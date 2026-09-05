"""Image preprocessing for paper scans. PDFs are unsupported in v1."""

from __future__ import annotations

import io

from PIL import Image, ImageOps

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_EDGE = 4096

Image.MAX_IMAGE_PIXELS = 100_000_000  # reject decompression bombs above ~100 MP


class AttachmentError(ValueError):
    """User-facing validation error for uploaded files."""


def prepare_image(data: bytes, filename: str) -> tuple[bytes, str]:
    """Return (encoded_bytes, mime_type) for a Discord image attachment."""
    lower_name = (filename or "").lower()
    if lower_name.endswith(".pdf"):
        raise AttachmentError(
            "PDF files are not supported yet. Upload a photo or scan of the page "
            "as a PNG, JPEG, or WebP image."
        )
    extension = lower_name.rsplit(".", 1)[-1] if "." in lower_name else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise AttachmentError(
            "Unsupported file type. Upload a PNG, JPEG, or WebP image."
        )
    if len(data) > MAX_FILE_BYTES:
        raise AttachmentError("The image is larger than Discord's 25 MB limit.")

    source = Image.open(io.BytesIO(data))
    try:
        format_name = (source.format or "").upper()
        if format_name not in {"PNG", "JPEG", "WEBP"}:
            raise AttachmentError(
                "Unsupported file type. Upload a PNG, JPEG, or WebP image."
            )
        image = ImageOps.exif_transpose(source)
        if image is source:
            image = source.copy()
        if max(image.size) > MAX_EDGE:
            image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
        if image.mode != "RGB":
            image = image.convert("RGB")

        output = io.BytesIO()
        if format_name == "PNG":
            image.save(output, format="PNG")
            mime_type = "image/png"
        else:
            image.save(output, format="JPEG", quality=90)
            mime_type = "image/jpeg"
        encoded = output.getvalue()
    except (OSError, Image.DecompressionBombError) as exc:
        raise AttachmentError("That file could not be read as an image.") from exc
    finally:
        source.close()

    if len(encoded) > MAX_FILE_BYTES:
        raise AttachmentError(
            "The processed image is still larger than 25 MB. "
            "Crop it or lower the resolution and try again."
        )
    return encoded, mime_type
