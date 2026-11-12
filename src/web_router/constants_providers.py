"""Messages about a provider name, kept apart from :mod:`web_router.constants`, so that file stops growing."""

from __future__ import annotations

MSG_PROVIDER_WRONG_METHOD = "Provider {name!r} does not serve this method. The providers that do: {available}."
"""A built-in provider asked for a method it has no backend for: ``brave`` for an extract, say."""
