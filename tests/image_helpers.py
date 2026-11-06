"""Images the screenshot tests hand a backend to return."""

from __future__ import annotations

import io

import pytest


def jpeg(width: int, height: int) -> bytes:
    """A real JPEG of the given size, as some vendors' browsers return for a PNG screenshot."""
    image = pytest.importorskip("PIL.Image")
    out = io.BytesIO()
    image.new("RGB", (width, height), "white").save(out, format="JPEG")
    return out.getvalue()
