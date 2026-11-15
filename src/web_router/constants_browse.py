"""The built-in browser-session and browse backends: each one's module, class, and the extra that installs it.

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

BUILTIN_BROWSER_PROVIDERS: dict[str, tuple[str, str, str]] = {
    Provider.STEEL.value: ("web_router.providers.steel", "SteelBrowserProvider", "all"),
    Provider.BROWSERBASE.value: ("web_router.providers.browserbase", "BrowserbaseBrowserProvider", "all"),
    Provider.HYPERBROWSER.value: ("web_router.providers.hyperbrowser", "HyperbrowserBrowserProvider", "all"),
    Provider.ANCHOR.value: ("web_router.providers.anchor", "AnchorBrowserProvider", "all"),
    Provider.KERNEL.value: ("web_router.providers.kernel", "KernelBrowserProvider", "all"),
    Provider.LOCAL.value: ("web_router.providers.local", "LocalBrowserProvider", "all"),
    Provider.AUTO.value: ("web_router.providers.auto_browser", "AutoBrowserProvider", "all"),
}
"""Opening a session needs only httpx, which ``all`` installs, so the vendors name ``all`` rather than ``browser``,
which is Playwright for the handle's page actions and not needed to open one. ``local`` needs only the standard
library, so its column is never quoted."""
