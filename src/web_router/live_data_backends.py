"""Which live data backends a router can use when no provider is named, and which one serves a source.

A built-in backend counts once it has a key, in the environment or in
``provider_config``; a custom one registered with
:func:`~web_router.register_live_data_provider` always does. The search over
every backend (:mod:`web_router.live_data_any`) asks them all; a run or a
description goes to the one backend a source names, or to the only one there is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from web_router.constants_live_data import LIVE_DATA_ENV_BY_PROVIDER
from web_router.constants_live_data_any import (
    MSG_LIVE_DATA_EVERY_BACKEND_FAILED,
    MSG_LIVE_DATA_NO_BACKEND,
    MSG_LIVE_DATA_OTHER_BACKEND,
    MSG_LIVE_DATA_WHICH_BACKEND,
)
from web_router.exceptions import ConfigurationError, ProviderNotInstalledError
from web_router.providers.base import LiveDataProvider
from web_router.registry import (
    available_live_data_providers,
    configured_live_data_providers,
    get_live_data_provider_class,
)
from web_router.types import Provider, provider_name
from web_router.types_live_data import LiveDataSource

if TYPE_CHECKING:
    from web_router.client_live_data import _LiveDataLookup

_KEY_SETTINGS = ("api_key", "client")
"""Settings that configure a backend in code, beside a ``key_argument`` of its own where it has one."""


def custom_live_data_backends() -> set[str]:
    """The live data backends registered in code rather than built in: always tried, since registering is the say-so."""
    return set(available_live_data_providers()) - set(LIVE_DATA_ENV_BY_PROVIDER)


def usable_backends(router: _LiveDataLookup, failures: dict[str, str] | None = None) -> list[LiveDataProvider]:
    """Every live data backend ``router`` can use, in name order: a key in the environment or in code.

    A custom backend registered with :func:`~web_router.register_live_data_provider`
    is tried too, since registering it is the caller's say-so. One that cannot
    be built -- no key after all, its extra not installed -- is left out, and
    named in ``failures`` when the caller passes a dict for them.
    """
    custom = custom_live_data_backends()
    in_env = set(configured_live_data_providers())
    backends: list[LiveDataProvider] = []
    for name in available_live_data_providers():
        if name not in custom and name not in in_env and not _keyed_in_code(router, name):
            continue
        try:
            backends.append(router.get_live_data_provider(name))
        except (ConfigurationError, ProviderNotInstalledError) as error:
            if failures is not None:
                failures[name] = str(error)
    return backends


def usable_backends_or_raise(router: _LiveDataLookup, failures: dict[str, str]) -> list[LiveDataProvider]:
    """:func:`usable_backends`, or the reason there are none: no key anywhere, or every keyed one failed to build."""
    if backends := usable_backends(router, failures):
        return backends
    if failures:
        raise ConfigurationError(MSG_LIVE_DATA_EVERY_BACKEND_FAILED.format(failures=failures))
    names = sorted({env_var for env_vars in LIVE_DATA_ENV_BY_PROVIDER.values() for env_var in env_vars})
    raise ConfigurationError(MSG_LIVE_DATA_NO_BACKEND.format(env_vars=", ".join(names)))


def backend_for(
    router: _LiveDataLookup,
    source: str | LiveDataSource,
    provider: str | Provider | LiveDataProvider | None,
) -> tuple[LiveDataProvider, str]:
    """The backend that runs or describes ``source``, and the name it knows the source by.

    A :class:`~web_router.LiveDataSource` names its own backend. A bare name
    needs ``provider``, unless this router can use exactly one live data
    backend, which is then the only place the name can be.

    Raises:
        ConfigurationError: ``provider`` contradicts the source's own, or a
            bare name could be on several backends, or on none.
    """
    if isinstance(source, LiveDataSource):
        if provider is not None and _name_of(provider) != source.provider:
            raise ConfigurationError(
                MSG_LIVE_DATA_OTHER_BACKEND.format(
                    source=source.name, owner=source.provider, provider=_name_of(provider)
                )
            )
        return router.get_live_data_provider(provider if provider is not None else source.provider), source.name
    if provider is not None:
        return router.get_live_data_provider(provider), source
    backends = usable_backends_or_raise(router, {})
    if len(backends) == 1:
        return backends[0], source
    raise ConfigurationError(
        MSG_LIVE_DATA_WHICH_BACKEND.format(source=source, providers=", ".join(backend.name for backend in backends))
    )


def _keyed_in_code(router: _LiveDataLookup, name: str) -> bool:
    """Whether ``provider_config`` hands built-in backend ``name`` a key or a client.

    The plain settings are read first, without importing the backend; its own
    ``key_argument`` only after, and a backend whose SDK is missing has none
    to read, so it was never a candidate.
    """
    config = router._config_for(name)
    if any(config.get(setting) for setting in _KEY_SETTINGS):
        return True
    try:
        provider_class = get_live_data_provider_class(name)
    except ProviderNotInstalledError:
        return False
    # Oxylabs names its Web API key `web_api_key`, as `api_key` there is extract's password.
    return bool(config.get(getattr(provider_class, "key_argument", "api_key")))


def _name_of(provider: str | Provider | LiveDataProvider) -> str:
    return provider.name if isinstance(provider, LiveDataProvider) else provider_name(provider)


__all__: list[str] = ["backend_for", "custom_live_data_backends", "usable_backends", "usable_backends_or_raise"]
