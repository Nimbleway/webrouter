"""Which backend each tool of an integration calls, decided once when the tools are built."""

from __future__ import annotations

from web_router.client import AsyncWebRouter, WebRouter
from web_router.constants import MSG_INTEGRATION_NO_EXTRACT_PROVIDER, PROVIDER_AUTO
from web_router.exceptions import ConfigurationError
from web_router.providers.auto_answer import AutoAnswerProvider
from web_router.registry import available_extract_providers
from web_router.registry_answer import available_answer_providers, default_answer_provider
from web_router.types import PresetComponent, Provider, provider_name


def tool_provider(provider: str | Provider | None) -> str | None:
    """A provider argument as the lowercase name the router looks it up by, or ``None`` for its default."""
    return provider_name(provider) if provider is not None else None


def _preset_provider(router: WebRouter | AsyncWebRouter | None, component: PresetComponent) -> str | None:
    """The backend the router's own preset names for ``component``, or ``None`` when it names none."""
    if router is None:
        return None
    provider = router.presets.resolve(component, None).provider
    return provider_name(provider) if provider is not None else None


def extract_provider_for(
    provider: str | Provider | None,
    extract_provider: str | Provider | None,
    *routers: WebRouter | AsyncWebRouter | None,
) -> str | None:
    """The extract tool's backend: the one named, the search one when it extracts, the router preset's, or the default.

    A router whose default cannot extract, and whose preset names no extract
    backend, raises here rather than on the agent's first page read, since no
    retry by the model fixes it -- and the tool never picks another backend the
    caller did not name.
    """
    if extract_provider is not None:
        return provider_name(extract_provider)
    name = tool_provider(provider)
    extractors = available_extract_providers()
    if name in extractors:
        return name
    for router in routers:
        preset = _preset_provider(router, "extract")
        if preset is not None:
            return preset
    for router in routers:
        if router is not None and router.default_provider not in extractors:
            raise ConfigurationError(
                MSG_INTEGRATION_NO_EXTRACT_PROVIDER.format(default=router.default_provider, available=extractors)
            )
    return None


def answer_provider_for(
    provider: str | Provider | None,
    answer_provider: str | Provider | None,
    router: WebRouter | AsyncWebRouter,
) -> str:
    """The answer tool's backend: the one named, the search one when it answers, the router preset's, or the default.

    The backend is resolved here, once, so every call goes to the same
    backend however the keys change, and a router with no answer backend to
    use raises :class:`~web_router.exceptions.NoAnswerProviderError` when the
    tools are built rather than on the agent's first question -- ``auto``
    included, when no answer backend has a key for it to try.
    """
    if answer_provider is not None:
        name = provider_name(answer_provider)
    elif (searched := tool_provider(provider)) in available_answer_providers():
        name = searched
    elif (preset := _preset_provider(router, "answer")) is not None:
        name = preset
    else:
        name = default_answer_provider(router.default_provider, router._config)
    if name == PROVIDER_AUTO:
        auto = router.get_answer_provider(name)
        if isinstance(auto, AutoAnswerProvider):
            auto.require_members()
    return name
