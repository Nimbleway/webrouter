"""Which backend each tool of an integration calls, decided once when the tools are built."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from web_router.client import AsyncWebRouter, WebRouter
from web_router.client_requests import with_timeout
from web_router.constants import MSG_INTEGRATION_NO_EXTRACT_PROVIDER, MSG_INTEGRATION_UNKNOWN_METHOD, PROVIDER_AUTO
from web_router.exceptions import ConfigurationError
from web_router.integrations._specs import KNOWN_METHODS
from web_router.providers.auto_answer import AutoAnswerProvider
from web_router.registry import available_extract_providers
from web_router.registry_answer import available_answer_providers, default_answer_provider
from web_router.types import Provider, provider_name


def tool_provider(provider: str | Provider | None) -> str | None:
    """A provider argument as the lowercase name the router looks it up by, or ``None`` for its default."""
    return provider_name(provider) if provider is not None else None


def extract_provider_for(
    provider: str | Provider | None,
    extract_provider: str | Provider | None,
    *routers: WebRouter | AsyncWebRouter | None,
) -> str | None:
    """The extract tool's backend: the one named, else the search one when it extracts, else the router's default.

    A router whose default cannot extract raises here rather than on the
    agent's first page read, since no retry by the model fixes it -- and the
    tool never picks another backend the caller did not name.
    """
    if extract_provider is not None:
        return provider_name(extract_provider)
    name = tool_provider(provider)
    extractors = available_extract_providers()
    if name in extractors:
        return name
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
    """The answer tool's backend: the one named, else the search one when it answers, else the router's default.

    The router's default is resolved here, once, so every call goes to the same
    backend however the keys change, and a router with no answer backend to
    use raises :class:`~web_router.exceptions.NoAnswerProviderError` when the
    tools are built rather than on the agent's first question -- ``auto``
    included, when no answer backend has a key for it to try.
    """
    if answer_provider is not None:
        name = provider_name(answer_provider)
    elif (searched := tool_provider(provider)) in available_answer_providers():
        name = searched
    else:
        name = default_answer_provider(router.default_provider, router._config)
    if name == PROVIDER_AUTO:
        auto = router.get_answer_provider(name)
        if isinstance(auto, AutoAnswerProvider):
            auto.require_members()
    return name


def backends(
    methods: Sequence[str],
    provider: str | Provider | None,
    extract_provider: str | Provider | None,
    answer_provider: str | Provider | None,
    router: WebRouter | AsyncWebRouter,
) -> tuple[str | None, str | None]:
    """The extract and answer tools' backends, resolved once; a tool not exposed keeps the name it was given."""
    extract = (
        extract_provider_for(provider, extract_provider, router)
        if "extract" in methods
        else tool_provider(extract_provider)
    )
    answer = (
        answer_provider_for(provider, answer_provider, router)
        if "answer" in methods
        else tool_provider(answer_provider)
    )
    return extract, answer


def exposed(methods: Sequence[str]) -> tuple[str, ...]:
    """``methods`` in the guide's order, once each is one a tool can be built for; an unknown one raises."""
    if unknown := [m for m in methods if m not in KNOWN_METHODS]:
        raise ValueError(MSG_INTEGRATION_UNKNOWN_METHOD.format(known=KNOWN_METHODS, method=unknown[0]))
    return tuple(m for m in KNOWN_METHODS if m in methods)


def call_options(
    search: dict[str, Any], *, answer: dict[str, Any], extract: dict[str, Any], timeout: float | None
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The search options as the caller set them, and each method's router keywords with ``timeout`` added.

    The search options stay without ``timeout``: they are what a ``SearchRequest`` takes.

    Raises:
        InvalidParameterError: for a ``timeout`` the router would refuse, here rather than on every call.
    """
    with_timeout(None, timeout)
    return search, {
        "search": {**search, "timeout": timeout},
        "answer": {**answer, "timeout": timeout},
        "extract": {**extract, "timeout": timeout},
    }
