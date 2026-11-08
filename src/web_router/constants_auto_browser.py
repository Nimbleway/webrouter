"""The options browser ``auto`` refuses whatever its members map, apart so ``constants.py`` stops growing."""

from __future__ import annotations

AUTO_BROWSER_OWN_OPTIONS = "provider_options"
"""One backend's own options, which browser ``auto`` refuses: ``local`` would read them as Chrome switches."""

AUTO_BROWSER_PROFILE_OPTIONS = ("profile", "persist_profile")
"""A saved profile belongs to one backend, so browser ``auto`` refuses both options."""
