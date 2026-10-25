"""Kernel browser sessions: endpoints, the field table and how a deleted session reads.

Kept apart from :mod:`web_router.constants`, as :mod:`web_router.constants_deepseek`
is, so that file stops growing. The env var and the provider's place in ``auto``
stay there, beside every other backend's.
"""

from __future__ import annotations

from typing import Any

from web_router.types import BrowserFieldRow, BrowserState, FieldPath, StealthLevel

# The Browsers API (kernel.sh/docs/api-reference/browsers). Checked live on
# 2026-10-20; see tests/providers/test_kernel_browser.py for what each call showed.

KERNEL_BASE_URL = "https://api.onkernel.com"
KERNEL_OPEN_PATH = "/browsers"
KERNEL_SESSION_PATH = "/browsers/{session_id}"
"""Read with ``GET``, ended with ``DELETE``, which answers a bare 204."""
KERNEL_SESSION_ID_FIELD = "session_id"

KERNEL_API_KEY_PREFIX = "Bearer "

KERNEL_STEALTH: dict[StealthLevel, dict[FieldPath, Any]] = {
    "off": {("stealth",): False},
    "low": {("stealth",): True},
    "high": {("stealth",): True},
}
"""Kernel has one stealth switch, so ``low`` and ``high`` both turn it on.

It also turns on Kernel's CAPTCHA solver and routes the session through
Kernel's stealth proxy: a live session opened with it reports
``proxy.mode: "default"`` where one without reports ``"direct"``.
"""

KERNEL_FIELDS: dict[str, BrowserFieldRow] = {
    "idle_timeout": ("timeout_seconds",),
    "stealth": KERNEL_STEALTH,
    "headless": ("headless",),
    "viewport": ("viewport",),
    "profile": ("profile", "id"),
    "persist_profile": ("profile", "save_changes"),
}
"""Normalized option -> where it goes in Kernel's body.

``timeout_seconds`` is an idle limit, not a lifetime: it counts down only while
no CDP, WebDriver or live-view client is connected, so it is ``idle_timeout``,
and ``timeout`` has nothing to go to. Kernel takes 10 seconds to 72 hours and
answers 400 outside that. ``viewport`` is placed whole, so Kernel picks the
refresh rate for the size. ``profile`` is the profile's id: Kernel answers
``profile not found`` for a name sent there, and a name goes through
``provider_options`` as ``profile.name``.
"""

KERNEL_CONTROL_URL_FIELD = "browser_live_view_url"
"""Kernel's live view, set for a session that is not headless.

Interactive: its ``readOnly=true`` is a query parameter whoever holds the URL
can take off again, so it is never treated as safe to show.
"""

KERNEL_SESSION_GONE_STATES: dict[str, BrowserState] = {
    "not_found": "released",
}
"""The error ``code`` Kernel answers a ``GET`` for a session it no longer has -> the normalized state.

Kernel reports no status on a session: one it still returns is running, and
one that was deleted -- by a release or by its idle limit, which Kernel does
not tell apart -- answers 404 ``not_found``. Read as ``released``, as Anchor's
``completed`` is, rather than as an ``expired`` nobody reported.
"""
