"""The routers' request builders: a method's keyword arguments in, its normalized request out.

Pure functions, kept apart so ``client.py`` stops growing. Both routers build
every request here, so the sync and async twins of a method can never send
different requests for the same arguments.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from web_router.types import (
    BrowseRequest,
    BrowserRequest,
    ExtractFormat,
    ExtractRequest,
    ResearchEffort,
    ResearchRequest,
    SearchDepth,
    SearchRequest,
    StealthLevel,
    Viewport,
)

JsonSchema = dict[str, Any] | type[BaseModel]
"""A schema for a structured answer: JSON Schema, or the pydantic model to take one from."""


def build_search_request(
    query: str,
    *,
    max_results: int | None,
    search_depth: SearchDepth | None,
    include_domains: list[str] | None,
    exclude_domains: list[str] | None,
    country: str | None,
    locale: str | None,
    provider_options: dict[str, Any] | None,
) -> SearchRequest:
    return SearchRequest(
        query=query,
        max_results=max_results,
        search_depth=search_depth,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        country=country,
        locale=locale,
        provider_options=provider_options or {},
    )


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
