"""Messages about a provider name, kept apart from :mod:`web_router.constants`, so that file stops growing."""

from __future__ import annotations

MSG_PROVIDER_WRONG_METHOD = "Provider {name!r} does not serve this method. The providers that do: {available}."
"""A built-in provider asked for a method it has no backend for: ``brave`` for an extract, say."""

MSG_OXYLABS_TWO_CREDENTIALS = (
    " OXYLABS_USERNAME and OXYLABS_PASSWORD are set, but those are the Realtime API's, which extract uses: live data"
    " runs on the Web API, whose key is a separate credential from the Oxylabs dashboard."
)
"""Added when Oxylabs' extract credentials are set and its live data key is not, so the two are not confused."""
