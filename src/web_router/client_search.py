"""``search()`` on the routers, kept apart so ``client.py`` stops growing.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` take these as
mixins, so ``router.search`` is where it always was. Beside the web results it
lists the data APIs whose catalog entries match the query, found locally and
never called (see :mod:`web_router.data_api_discovery`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from web_router.client_requests import build_search_request
from web_router.data_api_discovery import load_catalog, matches_only, with_matches
from web_router.types import SearchResponse

if TYPE_CHECKING:
    from web_router.client import AsyncWebRouter, WebRouter
    from web_router.providers.base import SearchProvider
    from web_router.routing import Budget
    from web_router.types import Provider, Recency, ResearchEffort, SearchDepth, SearchRequest
    from web_router.vendors.catalog import VendorCatalog


class _SearchPlan:
    """What both routers share: the data-APIs-only answer, or what a web search needs to run and be matched."""

    def _search_plan(
        self,
        query: str,
        provider: str | Provider | SearchProvider | None,
        max_results: int | None,
        budget: Budget | None,
        options: dict[str, Any],
        *,
        data_apis: bool,
        data_apis_only: bool,
        configured_only: bool,
    ) -> SearchResponse | _WebSearch:
        router = cast("WebRouter", self)
        if data_apis_only:
            return matches_only(
                query,
                router.vendor_credential_source(),
                data_apis=data_apis,
                configured_only=configured_only,
                max_results=max_results,
                web_options={**options, "provider": provider, "budget": budget},
            )
        # The catalog is read before the backend is called, so a broken one is known before anything is billed.
        catalog = load_catalog(data_apis)
        request = build_search_request(query, max_results=max_results, **options)
        return _WebSearch(router.get_provider(provider), request, catalog, configured_only)

    def _with_matches(self, response: SearchResponse, plan: _WebSearch) -> SearchResponse:
        source = cast("WebRouter", self).vendor_credential_source()
        return with_matches(response, source, catalog=plan.catalog, configured_only=plan.configured_only)


@dataclass(frozen=True)
class _WebSearch:
    chain: SearchProvider
    request: SearchRequest
    catalog: VendorCatalog | None
    configured_only: bool


class SyncSearch(_SearchPlan):
    def search(
        self,
        query: str,
        *,
        provider: str | Provider | SearchProvider | None = None,
        max_results: int | None = None,
        search_depth: SearchDepth | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        country: str | None = None,
        locale: str | None = None,
        recency: Recency | None = None,
        effort: ResearchEffort | None = None,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
        data_apis: bool = True,
        data_apis_only: bool = False,
        configured_only: bool = False,
    ) -> SearchResponse:
        """Search the web through ``provider`` (or the router's default).

        ``data_apis`` (on by default) also lists, in
        :attr:`~web_router.SearchResponse.data_apis`, the catalog endpoints
        whose words match the query -- found locally, at no cost, and never
        called: run one with :meth:`call_vendor`. ``configured_only`` keeps
        the ones this router holds credentials for. ``data_apis_only`` lists
        them alone and calls no web backend, so it refuses every web option
        but ``max_results``, which then caps the matches.
        """
        options = _options(search_depth, include_domains, exclude_domains, country, locale, recency, effort)
        plan = self._search_plan(
            query,
            provider,
            max_results,
            budget,
            {**options, "provider_options": provider_options},
            data_apis=data_apis,
            data_apis_only=data_apis_only,
            configured_only=configured_only,
        )
        if isinstance(plan, SearchResponse):
            return plan
        response = cast("WebRouter", self)._routed(plan.chain, plan.request, budget, plan.chain.search)
        return self._with_matches(response, plan)


class AsyncSearch(_SearchPlan):
    async def search(
        self,
        query: str,
        *,
        provider: str | Provider | SearchProvider | None = None,
        max_results: int | None = None,
        search_depth: SearchDepth | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        country: str | None = None,
        locale: str | None = None,
        recency: Recency | None = None,
        effort: ResearchEffort | None = None,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
        data_apis: bool = True,
        data_apis_only: bool = False,
        configured_only: bool = False,
    ) -> SearchResponse:
        """Search the web through ``provider`` (or the router's default), with the data APIs that match.

        The options are the sync router's; see :meth:`~web_router.WebRouter.search`.
        """
        options = _options(search_depth, include_domains, exclude_domains, country, locale, recency, effort)
        plan = self._search_plan(
            query,
            provider,
            max_results,
            budget,
            {**options, "provider_options": provider_options},
            data_apis=data_apis,
            data_apis_only=data_apis_only,
            configured_only=configured_only,
        )
        if isinstance(plan, SearchResponse):
            return plan
        response = await cast("AsyncWebRouter", self)._arouted(plan.chain, plan.request, budget, plan.chain.asearch)
        return self._with_matches(response, plan)


def _options(
    search_depth: SearchDepth | None,
    include_domains: list[str] | None,
    exclude_domains: list[str] | None,
    country: str | None,
    locale: str | None,
    recency: Recency | None,
    effort: ResearchEffort | None,
) -> dict[str, Any]:
    return {
        "search_depth": search_depth,
        "include_domains": include_domains,
        "exclude_domains": exclude_domains,
        "country": country,
        "locale": locale,
        "recency": recency,
        "effort": effort,
    }
