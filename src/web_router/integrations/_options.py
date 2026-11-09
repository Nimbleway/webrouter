"""Which backend each tool of an integration calls, decided once when the tools are built."""

from __future__ import annotations

from web_router.client import AsyncWebRouter, WebRouter
from web_router.constants import MSG_INTEGRATION_NO_EXTRACT_PROVIDER, PROVIDER_AUTO
from web_router.constants_presets import MSG_PRESET_TOOLS_EXTRACT_SPLIT
from web_router.exceptions import ConfigurationError
from web_router.providers.auto_answer import AutoAnswerProvider
from web_router.registry import available_extract_providers
from web_router.registry_answer import available_answer_providers, default_answer_provider
from web_router.types import Provider, provider_name
from web_router.types_meta import PresetComponent


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
    """The extract tool's backend: the one named, else ``None`` when a router's preset names one, else the search one.

    The search backend serves only when it extracts and no router's preset
    names an extract backend: a preset's is the router's own setting, and the
    search one sent in its place would override or conflict with it. ``None``
    sends each call to its router's preset backend, else its default. A router
    whose default cannot extract, and whose preset names no extract backend,
    raises here rather than on the agent's first page read, since no retry by
    the model fixes it -- and the tool never picks another backend the caller
    did not name. So does a sync and async pair where only one router's preset
    names an extract backend and the other would need one: no single answer
    fits both. A preset is read only when the answer turns on it, so one loaded
    after the router is built still serves.
    """
    if extract_provider is not None:
        return provider_name(extract_provider)
    name = tool_provider(provider)
    extractors = available_extract_providers()
    held = [router for router in routers if router is not None]
    if name in extractors:
        named = [_preset_provider(router, "extract") is not None for router in held]
        if not any(named):
            return name
        if not all(named):
            # The search backend would be right for one router and override the other's preset.
            raise ConfigurationError(MSG_PRESET_TOOLS_EXTRACT_SPLIT)
    for router in held:
        # None leaves each router its own preset's backend, resolved on every call: a sync and an async router
        # can hold different ones, and one name sent to both would override, or conflict with, the other's.
        if router.default_provider not in extractors and _preset_provider(router, "extract") is None:
            if any(_preset_provider(other, "extract") is not None for other in held if other is not router):
                raise ConfigurationError(MSG_PRESET_TOOLS_EXTRACT_SPLIT)
            raise ConfigurationError(
                MSG_INTEGRATION_NO_EXTRACT_PROVIDER.format(default=router.default_provider, available=extractors)
            )
    return None


def answer_provider_for(
    provider: str | Provider | None,
    answer_provider: str | Provider | None,
    router: WebRouter | AsyncWebRouter,
) -> str:
    """The answer tool's backend: the one named, then the router preset's, then the search one when it answers.

    With none of those it is the router's default answer backend.

    The backend is resolved here, once, so every call goes to the same
    backend however the keys change, and a router with no answer backend to
    use raises :class:`~web_router.exceptions.NoAnswerProviderError` when the
    tools are built rather than on the agent's first question -- ``auto``
    included, when no answer backend has a key for it to try.
    """
    if answer_provider is not None:
        name = provider_name(answer_provider)
    elif (preset := _preset_provider(router, "answer")) is not None:
        name = preset
    elif (searched := tool_provider(provider)) in available_answer_providers():
        name = searched
    else:
        name = default_answer_provider(router.default_provider, router._config)
    if name == PROVIDER_AUTO:
        auto = router.get_answer_provider(name)
        if isinstance(auto, AutoAnswerProvider):
            auto.require_members()
    return name
