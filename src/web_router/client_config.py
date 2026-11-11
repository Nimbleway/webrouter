"""The routers' ``provider_config``: the constructor kwargs each backend is built with.

Apart from :mod:`web_router.client` so that file stops growing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from web_router.constants import DELEGATING_PROVIDERS, MAX_INHERITS_FROM_AUTO, PROVIDER_AUTO, PROVIDER_MAX
from web_router.types import provider_name


def config_for(configs: Mapping[str, dict[str, Any]], key: str) -> dict[str, Any]:
    """Constructor kwargs for one backend: its ``provider_config`` entry, and every entry for a delegating one.

    Each of those builds its own members, so it is handed the config they
    would get by name -- a key passed in code reaches Nimble the same way
    through ``auto`` or ``max`` as through ``provider="nimble"``. ``max``
    races the members ``auto`` would try, so a ``chain`` or
    ``members_config`` set under ``auto`` is its own unless it sets one.
    """
    config = dict(configs.get(key, {}))
    if key == PROVIDER_MAX:
        shared = configs.get(PROVIDER_AUTO, {})
        for entry in MAX_INHERITS_FROM_AUTO:
            if entry in shared and entry not in config:
                config[entry] = shared[entry]
    if key in DELEGATING_PROVIDERS:
        siblings: dict[str, dict[str, Any]] = {name: c for name, c in configs.items() if name != key}
        # An explicit members_config is laid over the sibling entries, member by member, never in place of them.
        explicit: dict[str, dict[str, Any]] = {
            provider_name(name): dict(c) for name, c in (config.get("members_config") or {}).items()
        }
        config["members_config"] = {
            name: {**siblings.get(name, {}), **explicit.get(name, {})} for name in {*siblings, *explicit}
        }
    return config
