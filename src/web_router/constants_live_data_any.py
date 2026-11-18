"""Constants for live data with no provider named: one search over every backend, and runs routed by their source.

Kept apart from :mod:`web_router.constants_live_data` so that module stays
within its size limit. Read by :mod:`web_router.live_data_any` and
:mod:`web_router.live_data_backends`.
"""

from __future__ import annotations

from typing import Final

LIVE_DATA_EVERY_BACKEND: Final = "auto"
"""The ``provider`` of a catalogue page drawn from every configured backend; each source still names its own."""

MSG_LIVE_DATA_NO_BACKEND: Final = (
    "No live data backend is configured. Set one of {env_vars}, or pass its key in provider_config."
)

MSG_LIVE_DATA_WHICH_BACKEND: Final = (
    "Source {source!r} could be on any of {providers}: pass the LiveDataSource that live_data_sources() "
    "returned, or name its provider."
)

MSG_LIVE_DATA_OTHER_BACKEND: Final = (
    "Source {source!r} is {owner!r}'s, but provider={provider!r} was passed: a source's name belongs to one vendor."
)

MSG_LIVE_DATA_EVERY_BACKEND_FAILED: Final = "every live data backend failed: {failures}"
