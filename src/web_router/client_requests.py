"""The routers' request builders: a method's keyword arguments in, its normalized request out.

Pure functions, kept apart so ``client.py`` stops growing. Both routers build
every request here, so the sync and async twins of a method can never send
different requests for the same arguments. :func:`async_client_holders`, the
sync router's check of its ``provider_config``, lives here for the same reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from web_router.constants import AUTO_CLIENT_OPTION, DELEGATING_PROVIDERS
from web_router.types import (
    BrowseRequest,
    BrowserRequest,
    ExtractFormat,
    ExtractRequest,
    ResearchEffort,
    ResearchRequest,
    SearchRequest,
    StealthLevel,
    Viewport,
    provider_name,
)

if TYPE_CHECKING:
    from typing_extensions import Unpack

    from web_router.types_options import SearchOptions

JsonSchema = dict[str, Any] | type[BaseModel]
"""A schema for a structured answer: JSON Schema, or the pydantic model to take one from."""


def build_search_request(
    query: str, *, provider_options: dict[str, Any] | None, **options: Unpack[SearchOptions]
) -> SearchRequest:
    """Every search option goes through by name: a new one goes in the request model, signatures and SearchOptions."""
    return SearchRequest(query=query, provider_options=provider_options or {}, **options)


def build_extract_request(
    urls: str | list[str],
    *,
    format: ExtractFormat | None,
    provider_options: dict[str, Any] | None,
) -> ExtractRequest:
    return ExtractRequest(
        # One URL is the common case and reads better unwrapped; the
        # request model carries a list either way, so providers that batch
        # and providers that do not see the same shape.
        urls=[urls] if isinstance(urls, str) else list(urls),
        format=format,
        provider_options=provider_options or {},
    )


def build_research_request(
    query: str,
    *,
    output_schema: JsonSchema | None,
    records: list[dict[str, Any]] | None,
    effort: ResearchEffort | None,
    include_domains: list[str] | None,
    exclude_domains: list[str] | None,
    provider_options: dict[str, Any] | None,
) -> ResearchRequest:
    return ResearchRequest(
        query=query,
        output_schema=as_json_schema(output_schema),
        records=records,
        effort=effort,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        provider_options=provider_options or {},
    )


def build_browser_request(
    *,
    proxy_country: str | None,
    stealth: StealthLevel | None,
    solve_captchas: bool | None,
    profile: str | None,
    persist_profile: bool | None,
    timeout: float | None,
    idle_timeout: float | None,
    viewport: Viewport | None,
    headless: bool | None,
    provider_options: dict[str, Any] | None,
) -> BrowserRequest:
    return BrowserRequest(
        proxy_country=proxy_country,
        stealth=stealth,
        solve_captchas=solve_captchas,
        profile=profile,
        persist_profile=persist_profile,
        timeout=timeout,
        idle_timeout=idle_timeout,
        viewport=viewport,
        headless=headless,
        provider_options=provider_options or {},
    )


def build_browse_request(
    task: str,
    *,
    start_url: str | None,
    output_schema: JsonSchema | None,
    max_steps: int | None,
    allowed_domains: list[str] | None,
    provider_options: dict[str, Any] | None,
) -> BrowseRequest:
    return BrowseRequest(
        task=task,
        start_url=start_url,
        output_schema=as_json_schema(output_schema),
        max_steps=max_steps,
        allowed_domains=allowed_domains,
        provider_options=provider_options or {},
    )


def as_json_schema(schema: JsonSchema | None) -> dict[str, Any] | None:
    """Accept a pydantic model where a JSON Schema is expected.

    Callers describing a structured answer usually have a model already, and
    the request carries plain JSON Schema so it stays serializable -- so the
    conversion happens once, here, rather than at every call site.
    """
    if schema is None or isinstance(schema, dict):
        return schema
    return schema.model_json_schema()


def async_client_holders(config: dict[str, dict[str, Any]]) -> set[str]:
    """Every backend ``config`` hands an ``async_client``: by name, in ``auto``'s ``members_config``, or in its chain.

    A chain is a list of entries or a mapping from method to one, and an entry
    a bare name or ``(name, kwargs)`` -- only the latter can carry a client.
    """
    entries: list[tuple[str, Any]] = list(config.items())
    for delegating in DELEGATING_PROVIDERS:
        own = config.get(delegating, {})
        entries.extend((own.get("members_config") or {}).items())
        chain = own.get("chain") or ()
        for listed in chain.values() if isinstance(chain, Mapping) else (chain,):
            entries.extend(
                (item[0], item[1]) for item in listed or () if isinstance(item, (tuple, list)) and len(item) == 2
            )
    return {
        provider_name(name)
        for name, options in entries
        if isinstance(options, Mapping) and options.get(AUTO_CLIENT_OPTION)
    }
