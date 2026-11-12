"""The built-in browse backends: each one's module, class, and the extra that installs it.

Kept apart from :mod:`web_router.registry`, which may not grow, as the live data table is.
"""

from __future__ import annotations

from web_router.types import Provider

BUILTIN_BROWSE_PROVIDERS: dict[str, tuple[str, str, str]] = {
    Provider.BROWSER_USE.value: ("web_router.providers.browser_use", "BrowserUseBrowseProvider", "all"),
    Provider.BROWSERBASE.value: ("web_router.providers.browserbase", "BrowserbaseBrowseProvider", "all"),
    Provider.SKYVERN.value: ("web_router.providers.skyvern", "SkyvernBrowseProvider", "all"),
    Provider.AUTO.value: ("web_router.providers.auto", "AutoBrowseProvider", "all"),
}
"""Task backends are plain httpx, which the ``all`` extra installs, as for the browser table; ``auto`` too."""
