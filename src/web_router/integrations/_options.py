"""Which backend each tool of an integration calls, decided once when the tools are built."""

from __future__ import annotations

from web_router.client import AsyncWebRouter, WebRouter
from web_router.constants import MSG_INTEGRATION_NO_EXTRACT_PROVIDER
from web_router.exceptions import ConfigurationError
from web_router.registry import available_extract_providers
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
