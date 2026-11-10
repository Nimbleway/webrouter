"""Deprecated: the Nimble live data provider's old module, now :mod:`web_router.providers.nimble_live_data`.

``from web_router.providers.nimble_template import NimbleTemplateProvider``
still works, with a :class:`DeprecationWarning`, until the old names are removed.
"""

from __future__ import annotations

from web_router.live_data_compat import module_getattr

__getattr__ = module_getattr(__name__)
