"""A screenshot's PNG: one that is already PNG passes untouched, anything else is converted."""

from __future__ import annotations

import struct
import zlib

from tests.conftest import jpeg
from web_router.constants import PNG_SIGNATURE
from web_router.providers._png import as_png, png_size


def _png(width: int, height: int) -> bytes:
    """A real, minimal PNG header of the given size."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return PNG_SIGNATURE + struct.pack(">I", len(ihdr)) + chunk + struct.pack(">I", zlib.crc32(chunk))


def test_png_size_is_read_from_the_header() -> None:
    assert png_size(_png(1280, 720)) == (1280, 720)


def test_a_png_is_returned_untouched() -> None:
    png = _png(1280, 720)
    assert as_png(png) is png


def test_a_jpeg_comes_back_as_a_png_of_the_same_size() -> None:
    png = as_png(jpeg(64, 48))
    assert png.startswith(PNG_SIGNATURE)
    assert png_size(png) == (64, 48)
