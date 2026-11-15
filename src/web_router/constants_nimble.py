"""Nimble's fixed values that :mod:`web_router.constants` does not hold, kept apart so it does not grow."""

from __future__ import annotations

NIMBLE_AUTH_HEADER = "Authorization"
"""The header the SDK sends a key in; a keyless call omits it, which Nimble serves on its keyless tier."""
