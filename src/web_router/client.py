"""The router facade.

:class:`WebRouter` and :class:`AsyncWebRouter` hide which API is behind a call.
Point them at a provider name and call :meth:`WebRouter.search` or
:meth:`WebRouter.extract`; swapping backends is a one-word change at
construction, or a per-call ``provider=`` override.

A name does not imply every method: most search backends do not extract, and
some extract backends do not search. Each method resolves the name against its
own registry, so asking one for a backend that only serves another reports it
as unknown and lists the ones that method does serve.

:meth:`WebRouter.run_template` runs one of a vendor's templates -- a saved,
site-specific extraction that turns one page into a record -- and
:meth:`WebRouter.templates` and :meth:`WebRouter.template` list and describe
the catalogue it is picked from; the three share one registry, and all three
need ``provider`` named, since a template's name belongs to one vendor.

:meth:`WebRouter.research` behaves differently (:meth:`WebRouter.answer`, one
cited answer, does not): an agentic run takes minutes rather than milliseconds, so it waits
for the answer -- or hands back a :class:`~web_router.ResearchJob` through
:meth:`WebRouter.submit_research` when you would rather not sit on it.
:meth:`WebRouter.browse` and :meth:`WebRouter.submit_browse` are the same shape
with a hosted browser agent in place of a search index.

:meth:`WebRouter.open_browser` returns no answer but a handle on a cloud browser the caller
drives over CDP; the router releases every handle still open when it is closed.

:meth:`WebRouter.benchmark` is not a method of its own kind but a consumer of
:meth:`WebRouter.search`: the same query fanned out across several backends,
each row measured around the public call, so what is compared is what a
caller gets. :meth:`WebRouter.benchmark_extract` is the same consumer of
:meth:`WebRouter.extract`, the same URLs fanned out the same way.
"""

from __future__ import annotations

import threading
from collections.abc import Collection
from contextlib import AsyncExitStack, ExitStack, suppress
from types import TracebackType
from typing import Any, Generic, TypeVar

from web_router._benchmark import Stragglers
from web_router.client_answer import AsyncAnswer, SyncAnswer
from web_router.client_benchmark import AsyncBenchmark, SyncBenchmark
from web_router.client_budget import AsyncBudget, SyncBudget
from web_router.client_plan import AsyncPlan, SyncPlan
from web_router.client_providers import ProviderLookup
from web_router.client_requests import JsonSchema as JsonSchema
from web_router.client_requests import (
    async_client_holders,
    build_browse_request,
    build_browser_request,
    build_extract_request,
    build_research_request,
    build_search_request,
)
from web_router.client_templates import AsyncTemplates, SyncTemplates
from web_router.constants import (
    BROWSE_TIMEOUT,
    DEFAULT_PROVIDER,
    MSG_SYNC_ROUTER_ASYNC_CLIENT,
    RESEARCH_TIMEOUT,
)
from web_router.exceptions import ConfigurationError, ProviderNotFoundError
from web_router.presets import PresetRegistry
from web_router.providers.answer import AnswerProvider
from web_router.providers.base import (
    AsyncBrowserHandle,
    BrowseJob,
    BrowseProvider,
    BrowserHandle,
    BrowserProvider,
    ExtractProvider,
    ResearchJob,
    ResearchProvider,
    SearchProvider,
    TemplateProvider,
    _BaseProvider,
)
from web_router.registry_answer import every_provider_name
from web_router.routing import Budget, RouterLike, ToolName
from web_router.routing.routed import RouterSetup
from web_router.routing.submitted import asubmitted, submitted
from web_router.routing.url_policy import UrlPolicy
from web_router.types import (
    BrowseResponse,
    ExtractFormat,
    ExtractResponse,
    Provider,
    ResearchEffort,
    ResearchResponse,
    SearchDepth,
    SearchResponse,
    StealthLevel,
    Viewport,
    provider_name,
)
from web_router.types_presets import Preset

_HandleT = TypeVar("_HandleT", BrowserHandle, AsyncBrowserHandle)


class _BaseRouter(ProviderLookup, RouterSetup, Generic[_HandleT]):
    """Provider resolution and caching shared by the sync and async routers.

    Generic only in the browser handle each one hands out, which is the one
    thing the two routers hold on a caller's behalf.
    """

    def __init__(
        self,
        provider: str | Provider = DEFAULT_PROVIDER,
        *,
        provider_config: dict[str, dict[str, Any]] | None = None,
        router: RouterLike | None = None,
        route_tools: Collection[ToolName] | None = None,
        fail_on_router_error: bool = False,
        url_policy: UrlPolicy | None = None,
        preset: str | Preset | None = None,
    ) -> None:
        """
        Args:
            provider: Name of the default provider, e.g. ``"nimble"`` or
                :attr:`Provider.NIMBLE`. Omit it to let ``auto`` choose.
            provider_config: Constructor kwargs per provider, keyed by provider
                name -- ``{"tavily": {"api_key": ..., "timeout": 30}}``. This is
                the only place credentials are passed explicitly; everything
                else comes from the environment.
            router, route_tools, fail_on_router_error: a router that orders ``auto``'s backends, the tools that
                ask it (all five by default) and whether its failure raises (experimental) -- see
                :mod:`web_router.routing.invoke`. ``url_policy="public_only"`` refuses private hosts in a
                plan's or a router's URLs (experimental) -- see :mod:`web_router.routing.url_policy`.
            preset: The router's own preset, ``id@version`` or a
                :class:`~web_router.Preset`: applied to every call it applies
                to, beneath a call's own ``preset=`` and its arguments. A
                reference is looked up on each call, so its definition can be
                loaded into :attr:`presets` after the router is built. See
                :mod:`web_router.presets`.
        """
        self._attach_router(router, route_tools, fail_on_router_error, url_policy)
        name = provider_name(provider)
        # Either registry is enough to name a default: a router defaulted to a
        # search-only backend still extracts through `provider=`, and the
        # method that cannot serve the default says so when it is called.
        known = every_provider_name()
        if name not in known:
            raise ProviderNotFoundError(name, known)

        self.default_provider = name
        # Normalized on the way in so a config key matches the lookup in
        # get_provider, whichever form -- enum, or odd casing -- it was given in.
        self._config: dict[str, dict[str, Any]] = {
            provider_name(key): dict(config) for key, config in (provider_config or {}).items()
        }
        self.presets = PresetRegistry(client=preset, default_provider=name, provider_config=self._config)
        """The presets this router can use. Load definitions with :meth:`~web_router.PresetRegistry.load`."""
        self._instances: dict[str, SearchProvider] = {}
        # Keyed separately from the search instances: one name resolves to a
        # different class per method, and a vendor serving all three is three
        # objects here.
        self._extract_instances: dict[str, ExtractProvider] = {}
        self._template_instances: dict[str, TemplateProvider] = {}
        self._research_instances: dict[str, ResearchProvider] = {}
        self._answer_instances: dict[str, AnswerProvider] = {}
        self._browser_instances: dict[str, BrowserProvider] = {}
        self._browse_instances: dict[str, BrowseProvider] = {}
        # Every session this router opened that has not been seen released, so
        # close() can end the ones a caller lost track of rather than leave them
        # billing until the backend times them out.
        self._browser_handles: list[_HandleT] = []
        # A threading lock on both routers: the async one is as easily shared
        # across threads, each with its own loop, and nothing awaits while it
        # is held, so it can never stall an event loop.
        self._handles_lock = threading.Lock()
        # One lock for the instance caches: evaluate() calls benchmark()
        # from worker threads, so two rows can race the same check-then-create
        # and the loser's client would never be closed. Nothing awaits while it
        # is held.
        self._providers_lock = threading.Lock()
        # Rows the sync benchmark() gave up on whose calls still run; close()
        # waits for them so it never shuts a provider under a live search.
        self._stragglers = Stragglers()

    def _track(self, handle: _HandleT) -> _HandleT:
        # Pruned on every open, so a long-lived router that opens thousands of
        # sessions holds only the ones still open. In place and under the lock:
        # rebuilding the list and reassigning it lost a handle to a concurrent
        # open that appended between the two.
        with self._handles_lock:
            self._browser_handles[:] = [held for held in self._browser_handles if not held.released]
            self._browser_handles.append(handle)
        return handle

    def _open_handles(self) -> list[_HandleT]:
        # Left tracked: a handle leaves only once its release succeeds, so one
        # that fails is still here for the next close() to retry.
        with self._handles_lock:
            self._browser_handles[:] = [held for held in self._browser_handles if not held.released]
            return list(self._browser_handles)

    def _untrack(self, handle: _HandleT) -> None:
        with self._handles_lock, suppress(ValueError):
            self._browser_handles.remove(handle)

    def _all_instances(self) -> list[_BaseProvider]:
        return [
            *self._instances.values(),
            *self._extract_instances.values(),
            *self._template_instances.values(),
            *self._research_instances.values(),
            *self._answer_instances.values(),
            *self._browser_instances.values(),
            *self._browse_instances.values(),
        ]

    def __repr__(self) -> str:
        return f"<{type(self).__name__} default_provider={self.default_provider!r}>"


class WebRouter(_BaseRouter[BrowserHandle], SyncAnswer, SyncTemplates, SyncBenchmark, SyncPlan, SyncBudget):
    """Synchronous router.

    Example:
        >>> router = WebRouter("nimble")
        >>> response = router.search("who won the 2022 world cup", max_results=5)
        >>> response.urls
        >>> router.extract(response.urls[:3], format="markdown")

    ``budget=`` caps one call and :meth:`budget` several (experimental): see :mod:`web_router.routing.budget`.
    """

    def __init__(
        self,
        provider: str | Provider = DEFAULT_PROVIDER,
        *,
        provider_config: dict[str, dict[str, Any]] | None = None,
        router: RouterLike | None = None,
        route_tools: Collection[ToolName] | None = None,
        fail_on_router_error: bool = False,
        url_policy: UrlPolicy | None = None,
        preset: str | Preset | None = None,
    ) -> None:
        """
        Raises:
            ConfigurationError: for a backend handed an ``async_client``, which
                this router, calling synchronously, could never use: through
                ``auto`` the call would quietly go to another backend instead.
        """
        super().__init__(
            provider,
            provider_config=provider_config,
            router=router,
            route_tools=route_tools,
            fail_on_router_error=fail_on_router_error,
            url_policy=url_policy,
            preset=preset,
        )
        async_only = sorted(async_client_holders(self._config))
        if async_only:
            raise ConfigurationError(MSG_SYNC_ROUTER_ASYNC_CLIENT.format(names=", ".join(map(repr, async_only))))

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
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
        preset: str | Preset | None = None,
    ) -> SearchResponse:
        """Search the web through ``provider`` (or the router's default).

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "search",
            preset,
            provider=provider,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            provider_options=provider_options,
        )
        request = build_search_request(query, **plan.options)
        chain = self.get_provider(plan.provider)
        with plan.explained():
            response = self._routed(chain, request, budget, chain.search)
        return plan.stamp(response)

    def extract(
        self,
        urls: str | list[str],
        *,
        provider: str | Provider | ExtractProvider | None = None,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
        preset: str | Preset | None = None,
    ) -> ExtractResponse:
        """Fetch the contents of one or more URLs through ``provider`` (or the router's default).

        Several URLs can partly succeed: what came back is in
        :attr:`~web_router.ExtractResponse.documents` and everything that did
        not is in :attr:`~web_router.ExtractResponse.failures`. A call where
        nothing came back raises instead.

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "extract",
            preset,
            provider=provider,
            format=format,
            provider_options=provider_options,
        )
        request = build_extract_request(urls, **plan.options)
        chain = self.get_extract_provider(plan.provider)
        with plan.explained():
            response = self._routed(chain, request, budget, chain.extract)
        return plan.stamp(response)

    def research(
        self,
        query: str,
        *,
        provider: str | Provider | ResearchProvider | None = None,
        output_schema: JsonSchema | None = None,
        records: list[dict[str, Any]] | None = None,
        effort: ResearchEffort | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        timeout: float | None = RESEARCH_TIMEOUT,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
        preset: str | Preset | None = None,
    ) -> ResearchResponse:
        """Research a question through ``provider`` (or the router's default), and wait.

        Unlike :meth:`search`, this is a job: the backend reasons over several
        turns and answers in minutes rather than milliseconds. It blocks until
        it has an answer, so do not call it on a request thread -- use
        :meth:`submit_research` there and poll the job.

        Passing ``output_schema`` asks for a structured answer instead of prose,
        as JSON Schema or as the pydantic model to take one from. Adding
        ``records`` on top of it enriches rows you already hold, one filled-in
        object per row.

        Args:
            timeout: Seconds to wait. ``None`` waits as long as the run takes.
                Running out raises :class:`~web_router.ResearchTimeoutError` and
                leaves the run going rather than cancelling it.
            preset: A saved configuration for this call; see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "research",
            preset,
            provider=provider,
            output_schema=output_schema,
            effort=effort,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider_options=provider_options,
        )
        request = build_research_request(query, records=records, **plan.options)
        chain = self.get_research_provider(plan.provider)
        with plan.explained():
            response = self._routed(chain, request, budget, lambda r: chain.research(r, timeout=timeout))
        return plan.stamp(response)

    def submit_research(
        self,
        query: str,
        *,
        provider: str | Provider | ResearchProvider | None = None,
        output_schema: JsonSchema | None = None,
        records: list[dict[str, Any]] | None = None,
        effort: ResearchEffort | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        provider_options: dict[str, Any] | None = None,
        preset: str | Preset | None = None,
    ) -> ResearchJob:
        """Start a research run and hand back the job, without waiting for it.

        The same call as :meth:`research` up to the point the backend accepts the run. What comes back
        polls with :meth:`~web_router.ResearchJob.status` and blocks with :meth:`~web_router.ResearchJob.result`,
        so a caller with something else to do can go and do it.

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "research",
            preset,
            provider=provider,
            output_schema=output_schema,
            effort=effort,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider_options=provider_options,
        )
        request = build_research_request(query, records=records, **plan.options)
        with plan.explained():
            job = submitted("research", self.get_research_provider(plan.provider), request, self)
        job._pin(plan.meta("pending"))
        return job

    def browse(
        self,
        task: str,
        *,
        provider: str | Provider | BrowseProvider | None = None,
        start_url: str | None = None,
        output_schema: JsonSchema | None = None,
        max_steps: int | None = None,
        allowed_domains: list[str] | None = None,
        timeout: float | None = BROWSE_TIMEOUT,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
        preset: str | Preset | None = None,
    ) -> BrowseResponse:
        """Hand a task to a hosted browser agent through ``provider`` (or the router's default), and wait.

        A job, like :meth:`research`: the vendor's agent drives one of its own
        browsers through the task and answers in minutes. It blocks until it
        has an answer -- use :meth:`submit_browse` where that would hold a
        request thread.

        Args:
            start_url: The page to start on. A backend that cannot find a first
                page itself raises :class:`~web_router.MissingParameterError`
                without it, before the task starts.
            output_schema: Ask for a structured answer instead of prose, as
                JSON Schema or as the pydantic model to take one from.
            timeout: Seconds to wait. ``None`` waits as long as the task takes.
                Running out raises :class:`~web_router.BrowseTimeoutError` and
                leaves the task going rather than cancelling it.
            preset: A saved configuration for this call; see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "browse",
            preset,
            provider=provider,
            start_url=start_url,
            output_schema=output_schema,
            max_steps=max_steps,
            allowed_domains=allowed_domains,
            provider_options=provider_options,
        )
        request = build_browse_request(task, **plan.options)
        chain = self.get_browse_provider(plan.provider)
        with plan.explained():
            response = self._routed(chain, request, budget, lambda r: chain.browse(r, timeout=timeout))
        return plan.stamp(response)

    def submit_browse(
        self,
        task: str,
        *,
        provider: str | Provider | BrowseProvider | None = None,
        start_url: str | None = None,
        output_schema: JsonSchema | None = None,
        max_steps: int | None = None,
        allowed_domains: list[str] | None = None,
        provider_options: dict[str, Any] | None = None,
        preset: str | Preset | None = None,
    ) -> BrowseJob:
        """Start a browse task and hand back the job, without waiting for it.

        The same call as :meth:`browse` up to the point the backend accepts the
        task; the job polls with :meth:`~web_router.BrowseJob.status` and blocks
        with :meth:`~web_router.BrowseJob.result`.

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "browse",
            preset,
            provider=provider,
            start_url=start_url,
            output_schema=output_schema,
            max_steps=max_steps,
            allowed_domains=allowed_domains,
            provider_options=provider_options,
        )
        request = build_browse_request(task, **plan.options)
        with plan.explained():
            job = submitted("browse", self.get_browse_provider(plan.provider), request, self)
        job._pin(plan.meta("pending"))
        return job

    def open_browser(
        self,
        provider: str | Provider | BrowserProvider | None = None,
        *,
        proxy_country: str | None = None,
        stealth: StealthLevel | None = None,
        solve_captchas: bool | None = None,
        profile: str | None = None,
        persist_profile: bool | None = None,
        timeout: float | None = None,
        idle_timeout: float | None = None,
        viewport: Viewport | None = None,
        headless: bool | None = None,
        provider_options: dict[str, Any] | None = None,
        preset: str | Preset | None = None,
    ) -> BrowserHandle:
        """Open a browser session through ``provider`` (or the router's default).

        Returns a handle rather than an answer: connect Playwright, browser-use
        or Stagehand to :attr:`~web_router.BrowserHandle.cdp_url` and drive it.
        Use the handle as a context manager so the session is released however
        the block ends -- it is billed until it is::

            with router.open_browser(timeout=300) as browser:
                page = playwright.chromium.connect_over_cdp(browser.cdp_url)

        Args:
            stealth: How hard the backend hides the automation: ``"off"``,
                ``"low"`` or ``"high"`` -- see :data:`~web_router.StealthLevel`.
                Unset keeps the backend's own default. A backend without the
                level raises :class:`~web_router.UnsupportedParameterError`
                rather than serving another one, and one that serves it only
                beside another option raises
                :class:`~web_router.MissingParameterError` when that one is unset.
            timeout: Seconds the session may live. Left unset, a backend that
                maps a lifetime limit is sent
                :data:`~web_router.constants.BROWSER_TIMEOUT` rather than its
                own default, which on some is hours. The only way past that
                default is a larger ``timeout``, passed here or set by a preset.
            preset: A saved configuration for this call; see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "browser",
            preset,
            provider=provider,
            proxy_country=proxy_country,
            stealth=stealth,
            solve_captchas=solve_captchas,
            profile=profile,
            persist_profile=persist_profile,
            timeout=timeout,
            idle_timeout=idle_timeout,
            viewport=viewport,
            headless=headless,
            provider_options=provider_options,
        )
        request = build_browser_request(**plan.options)
        with plan.explained():
            handle = self.get_browser_provider(plan.provider).open(request)
        handle._pin(plan.meta("passed"))
        return self._track(handle)

    def close(self) -> None:
        """Release every browser session still open, then close every provider this router built.

        A provider passed in already constructed is the caller's to close; this
        router never cached it and must not shut its client down. Sessions are
        released whichever provider opened them, since this router opened them.
        A session whose release fails stays tracked, so calling this again
        retries it. A call a benchmark timed out on may still be running on
        its thread; it is waited for first -- bounded by that provider's own
        HTTP timeout -- so no client is closed under a live call.
        """
        self._stragglers.join()
        with ExitStack() as stack:
            # Callbacks run last-in, first-out: the providers are pushed first
            # so they close after the releases, which need their clients. A
            # release that fails does not stop the rest.
            for instance in self._all_instances():
                stack.callback(instance.close)
            for handle in self._open_handles():
                stack.callback(self._release_tracked, handle)

    def _release_tracked(self, handle: BrowserHandle) -> None:
        handle.release()
        self._untrack(handle)

    def __enter__(self) -> WebRouter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class AsyncWebRouter(
    _BaseRouter[AsyncBrowserHandle], AsyncAnswer, AsyncTemplates, AsyncBenchmark, AsyncPlan, AsyncBudget
):
    """Asynchronous router.

    Example:
        >>> async with AsyncWebRouter("nimble") as router:
        ...     response = await router.search("latest nvidia earnings")
    """

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
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
        preset: str | Preset | None = None,
    ) -> SearchResponse:
        """Search the web through ``provider`` (or the router's default).

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "search",
            preset,
            provider=provider,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            provider_options=provider_options,
        )
        request = build_search_request(query, **plan.options)
        chain = self.get_provider(plan.provider)
        with plan.explained():
            response = await self._arouted(chain, request, budget, chain.asearch)
        return plan.stamp(response)

    async def extract(
        self,
        urls: str | list[str],
        *,
        provider: str | Provider | ExtractProvider | None = None,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
        preset: str | Preset | None = None,
    ) -> ExtractResponse:
        """Fetch the contents of one or more URLs through ``provider`` (or the router's default).

        Backends that take one URL per call are fanned out concurrently. As on
        the sync router, a partly successful call returns its failures and one
        where nothing came back raises.

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "extract",
            preset,
            provider=provider,
            format=format,
            provider_options=provider_options,
        )
        request = build_extract_request(urls, **plan.options)
        chain = self.get_extract_provider(plan.provider)
        with plan.explained():
            response = await self._arouted(chain, request, budget, chain.aextract)
        return plan.stamp(response)

    async def research(
        self,
        query: str,
        *,
        provider: str | Provider | ResearchProvider | None = None,
        output_schema: JsonSchema | None = None,
        records: list[dict[str, Any]] | None = None,
        effort: ResearchEffort | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        timeout: float | None = RESEARCH_TIMEOUT,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
        preset: str | Preset | None = None,
    ) -> ResearchResponse:
        """Research a question through ``provider`` (or the router's default), and await it.

        As on the sync router, except that waiting gives the event loop back
        rather than holding a thread -- which is what makes running several
        research questions at once reasonable here and not there.

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "research",
            preset,
            provider=provider,
            output_schema=output_schema,
            effort=effort,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider_options=provider_options,
        )
        request = build_research_request(query, records=records, **plan.options)
        chain = self.get_research_provider(plan.provider)
        with plan.explained():
            response = await self._arouted(chain, request, budget, lambda r: chain.aresearch(r, timeout=timeout))
        return plan.stamp(response)

    async def submit_research(
        self,
        query: str,
        *,
        provider: str | Provider | ResearchProvider | None = None,
        output_schema: JsonSchema | None = None,
        records: list[dict[str, Any]] | None = None,
        effort: ResearchEffort | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        provider_options: dict[str, Any] | None = None,
        preset: str | Preset | None = None,
    ) -> ResearchJob:
        """Start a research run and hand back the job, without awaiting it.

        Await :meth:`~web_router.ResearchJob.aresult` when you want the answer.

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "research",
            preset,
            provider=provider,
            output_schema=output_schema,
            effort=effort,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider_options=provider_options,
        )
        request = build_research_request(query, records=records, **plan.options)
        with plan.explained():
            job = await asubmitted("research", self.get_research_provider(plan.provider), request, self)
        job._pin(plan.meta("pending"))
        return job

    async def browse(
        self,
        task: str,
        *,
        provider: str | Provider | BrowseProvider | None = None,
        start_url: str | None = None,
        output_schema: JsonSchema | None = None,
        max_steps: int | None = None,
        allowed_domains: list[str] | None = None,
        timeout: float | None = BROWSE_TIMEOUT,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
        preset: str | Preset | None = None,
    ) -> BrowseResponse:
        """Hand a task to a hosted browser agent through ``provider`` (or the router's default), and await it.

        As on the sync router, except that waiting gives the event loop back.

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "browse",
            preset,
            provider=provider,
            start_url=start_url,
            output_schema=output_schema,
            max_steps=max_steps,
            allowed_domains=allowed_domains,
            provider_options=provider_options,
        )
        request = build_browse_request(task, **plan.options)
        chain = self.get_browse_provider(plan.provider)
        with plan.explained():
            response = await self._arouted(chain, request, budget, lambda r: chain.abrowse(r, timeout=timeout))
        return plan.stamp(response)

    async def submit_browse(
        self,
        task: str,
        *,
        provider: str | Provider | BrowseProvider | None = None,
        start_url: str | None = None,
        output_schema: JsonSchema | None = None,
        max_steps: int | None = None,
        allowed_domains: list[str] | None = None,
        provider_options: dict[str, Any] | None = None,
        preset: str | Preset | None = None,
    ) -> BrowseJob:
        """Start a browse task and hand back the job, without awaiting it.

        Await :meth:`~web_router.BrowseJob.aresult` when you want the answer.

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "browse",
            preset,
            provider=provider,
            start_url=start_url,
            output_schema=output_schema,
            max_steps=max_steps,
            allowed_domains=allowed_domains,
            provider_options=provider_options,
        )
        request = build_browse_request(task, **plan.options)
        with plan.explained():
            job = await asubmitted("browse", self.get_browse_provider(plan.provider), request, self)
        job._pin(plan.meta("pending"))
        return job

    async def open_browser(
        self,
        provider: str | Provider | BrowserProvider | None = None,
        *,
        proxy_country: str | None = None,
        stealth: StealthLevel | None = None,
        solve_captchas: bool | None = None,
        profile: str | None = None,
        persist_profile: bool | None = None,
        timeout: float | None = None,
        idle_timeout: float | None = None,
        viewport: Viewport | None = None,
        headless: bool | None = None,
        provider_options: dict[str, Any] | None = None,
        preset: str | Preset | None = None,
    ) -> AsyncBrowserHandle:
        """Open a browser session through ``provider`` (or the router's default).

        Awaited for the handle, like :meth:`submit_research` is for its job,
        and the handle is then an async context manager::

            async with await router.open_browser() as browser:
                page = await playwright.chromium.connect_over_cdp(browser.cdp_url)

        ``stealth`` takes the same levels, and ``timeout`` defaults, as on the
        sync router; again the only way past that default is a larger one,
        passed here or set by a preset.

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve(
            "browser",
            preset,
            provider=provider,
            proxy_country=proxy_country,
            stealth=stealth,
            solve_captchas=solve_captchas,
            profile=profile,
            persist_profile=persist_profile,
            timeout=timeout,
            idle_timeout=idle_timeout,
            viewport=viewport,
            headless=headless,
            provider_options=provider_options,
        )
        request = build_browser_request(**plan.options)
        with plan.explained():
            handle = await self.get_browser_provider(plan.provider).aopen(request)
        handle._pin(plan.meta("passed"))
        return self._track(handle)

    async def aclose(self) -> None:
        """Release every browser session still open, then close every provider this router built.

        The same ownership rule and ordering as :meth:`WebRouter.close`.
        """
        async with AsyncExitStack() as stack:
            for instance in self._all_instances():
                stack.push_async_callback(instance.aclose)
            for handle in self._open_handles():
                stack.push_async_callback(self._release_tracked, handle)

    async def _release_tracked(self, handle: AsyncBrowserHandle) -> None:
        await handle.release()
        self._untrack(handle)

    async def __aenter__(self) -> AsyncWebRouter:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
