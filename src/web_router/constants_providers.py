"""Messages about provider names and missing extras, apart from :mod:`web_router.constants` so it stops growing."""

from __future__ import annotations

from web_router.constants import INSTALL_EXTRA

MSG_PROVIDER_WRONG_METHOD = "Provider {name!r} does not serve this method. The providers that do: {available}."
"""A built-in provider asked for a method it has no backend for: ``brave`` for an extract, say."""

MSG_EXTRA_MISSING = "{what} needs the {module!r} package, which the {extra!r} extra installs: " + INSTALL_EXTRA + "."
"""A missing optional dependency, named with the install line that fixes it."""

HTTPX_MODULE = "httpx"
"""The HTTP client the REST-backed providers need, which every method extra installs."""
