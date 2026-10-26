"""A screenshot's PNG: the format check, the conversion some backends need, and the size from its header."""

from __future__ import annotations

import io
import struct

from web_router.constants import EXTRA_BROWSER, MSG_BROWSER_ACTIONS_NOT_INSTALLED, PNG_SIGNATURE, PNG_SIZE_OFFSET
from web_router.exceptions import ConfigurationError


def as_png(image: bytes) -> bytes:
    """The capture as PNG. Some vendors' browsers answer a PNG request with another format -- Anchor's
    with JPEG, even over raw CDP -- so it is converted here, and ``Screenshot.png`` holds a PNG on every backend."""
    if image.startswith(PNG_SIGNATURE):
        return image
    try:
        from PIL import Image
    except ImportError as exc:
        raise ConfigurationError(MSG_BROWSER_ACTIONS_NOT_INSTALLED.format(extra=EXTRA_BROWSER)) from exc
    converted = io.BytesIO()
    with Image.open(io.BytesIO(image)) as picture:
        picture.save(converted, format="PNG")
    return converted.getvalue()


def png_size(png: bytes) -> tuple[int, int]:
    """A PNG's width and height in pixels, read from its header."""
    width, height = struct.unpack(">II", png[PNG_SIZE_OFFSET : PNG_SIZE_OFFSET + 8])
    return int(width), int(height)
