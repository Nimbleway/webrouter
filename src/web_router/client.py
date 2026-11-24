"""The router facade.

:class:`WebRouter` and :class:`AsyncWebRouter` hide which API is behind a call. Point them at a provider name and
call :meth:`WebRouter.search` or :meth:`WebRouter.extract`; swapping backends is a one-word change at construction,
or a per-call ``provider=`` override.

A name does not imply every method: most search backends do not extract, and some extract backends do not search.
Each method resolves the name against its own registry, so asking one for a backend that only serves another
reports it as unknown and lists the ones that method does serve.

:meth:`WebRouter.live_data` runs one of a vendor's ready-made scrapers for one
site and returns structured records fetched for this call;
:meth:`WebRouter.live_data_sources` and :meth:`WebRouter.live_data_source`
list and describe the catalogue (``client_live_data.py``).

:meth:`WebRouter.research` behaves differently (:meth:`WebRouter.answer`, one cited answer, does not): an agentic
run takes minutes rather than milliseconds, so it waits for the answer -- or hands back a
:class:`~web_router.ResearchJob` through :meth:`WebRouter.submit_research` when you would rather not sit on it.
:meth:`WebRouter.browse` and :meth:`WebRouter.submit_browse` are the same shape with a hosted browser agent in
place of a search index.

:meth:`WebRouter.open_browser` returns no answer but a handle on a cloud browser the caller drives over CDP; the
router releases every handle still open when it is closed.

:meth:`WebRouter.benchmark` is not a method of its own kind but a consumer of :meth:`WebRouter.search`: the same
query fanned out across several backends, each row measured around the public call, so what is compared is what a
caller gets. :meth:`WebRouter.benchmark_extract` is the same consumer of :meth:`WebRouter.extract`.
"""

from __future__ import annotations

import threading
from collections.abc import Collection, Mapping
from contextlib import AsyncExitStack, ExitStack
from types import TracebackType
from typing import Any, Literal

from web_router._benchmark import Stragglers
from web_router.client_answer import AsyncAnswer, SyncAnswer
from web_router.client_benchmark import AsyncBenchmark, SyncBenchmark
from web_router.client_budget import AsyncBudget, SyncBudget
from web_router.client_config import config_for
from web_router.client_core import AsyncCore, SyncCore
from web_router.client_handles import _HandleT, _HandleTracking
from web_router.client_live_data import AsyncLiveData, SyncLiveData
from web_router.client_plan import AsyncPlan, SyncPlan
from web_router.client_requests import JsonSchema as JsonSchema
from web_router.client_requests import (
    async_client_holders,
    build_browse_request,
    build_browser_request,
    build_research_request,
)
from web_router.client_search import AsyncSearch, SyncSearch
from web_router.client_vendors import AsyncVendors, DataApiCredentials, SyncVendors, VendorCredentials, _VendorLookup
from web_router.client_vendors_compat import renamed_credentials
from web_router.client_warmup import AsyncWarmup, SyncWarmup
from web_router.constants import BROWSE_TIMEOUT, DEFAULT_PROVIDER, MSG_SYNC_ROUTER_ASYNC_CLIENT, RESEARCH_TIMEOUT
from web_router.constants_data_driven import DATA_DRIVEN_ROUTER_NAME
from web_router.exceptions import ConfigurationError, ProviderNotFoundError
from web_router.providers._pinning import backend_of
from web_router.providers._runtime import Runtime
from web_router.providers.answer import AnswerProvider
from web_router.providers.base import (
    AsyncBrowserHandle,
    BrowseJob,
    BrowseProvider,
    BrowserHandle,
    BrowserProvider,
    ExtractProvider,
    LiveDataProvider,
    ResearchJob,
    ResearchProvider,
    SearchProvider,
    _BaseProvider,
)
from web_router.registry import (
    create_browse_provider,
    create_browser_provider,
    create_extract_provider,
    create_provider,
    create_research_provider,
)
from web_router.registry_answer import every_provider_name
from web_router.routing import Budget, RouterLike, ToolName
from web_router.routing.router_setup import RouterSetup
from web_router.routing.submitted import asubmitted, submitted
from web_router.routing.url_policy import UrlPolicy
from web_router.types import (
    BrowseResponse,
    Provider,
    ResearchEffort,
    ResearchResponse,
    StealthLevel,
    Viewport,
    provider_name,
)


class _BaseRouter(RouterSetup, _VendorLookup, _HandleTracking[_HandleT]):
    """Provider resolution and caching shared by the sync and async routers.

    Generic only in the browser handle each one hands out: the one thing the two routers hold for a caller.
    """

    def __init__(
        self,
        provider: str | Provider = DEFAULT_PROVIDER,
        *,
        provider_config: dict[str, dict[str, Any]] | None = None,
        router: RouterLike | str | None = DATA_DRIVEN_ROUTER_NAME,
        router_config: Mapping[str, Any] | None = None,
        route_tools: Collection[ToolName] | None = None,
        route_modes: bool | None = None,
        fail_on_router_error: bool = False,
        url_policy: UrlPolicy | None = None,
        data_api_credentials: DataApiCredentials = None,
        vendor_credentials: VendorCredentials = None,
        max_concurrency: int | None = None,
        hop_timeout: float | Literal[False] | None = None,
        timeout_cooldown: bool = True,
    ) -> None:
        """
        Args:
            provider: Name of the default provider, e.g. ``"nimble"`` or :attr:`Provider.NIMBLE`; omit it for ``auto``.
            provider_config: Constructor kwargs per provider, keyed by provider name: ``{"tavily": {"api_key": ...}}``.
                This is the only place credentials are passed explicitly; everything else comes from the environment.
            router, route_tools, fail_on_router_error: a router that orders ``auto``'s backends, the tools that
                ask it (the router's own ``route_tools``, else all five) and if its failure raises (experimental):
                ``"data-driven"`` unless set (:mod:`web_router.routing.data_driven`), ``None`` for none. A name builds a
                registered router from ``router_config``, closed with this one (:mod:`web_router.routing.registry`).
                ``route_modes=True`` shows it a candidate per vendor mode too (the router's own, else no).
                ``url_policy="public_only"`` refuses private hosts in a plan's or a router's URLs (experimental).
            data_api_credentials: for :meth:`call_data_api`, read before the environment; once ``vendor_credentials``.
            max_concurrency: the most vendor calls this router has in flight at once, across every search,
                extract and answer it serves (per event loop on the async router); ``None`` caps nothing. A call
                waits for a slot within its own ``timeout`` -- see :mod:`web_router.providers._runtime`.
            hop_timeout, timeout_cooldown: an ``auto`` hop's cap (half the time left unless set; ``False`` for none),
                and a cooldown after timeouts in a row -- see :mod:`web_router.providers._hop`.
        """
        self._runtime = Runtime.capped(max_concurrency, hop_timeout, timeout_cooldown)
        self._init_vendors(renamed_credentials(data_api_credentials, vendor_credentials))
        name = provider_name(provider)
        # Either registry names a default (a pinned search id by its backend); a method that can't serve it says so.
        known = every_provider_name()
        if backend_of(name) not in known:
            raise ProviderNotFoundError(name, known)
        self.default_provider = name
        # Normalized on the way in so a config key matches get_provider's lookup, whatever form it was given in.
        self._config: dict[str, dict[str, Any]] = {
            provider_name(key): dict(config) for key, config in (provider_config or {}).items()
        }
        self._instances: dict[str, SearchProvider] = {}
        # Keyed apart from the search instances: one name resolves to a different class per method.
        self._extract_instances: dict[str, ExtractProvider] = {}
        self._live_data_instances: dict[str, LiveDataProvider] = {}
        self._research_instances: dict[str, ResearchProvider] = {}
        self._answer_instances: dict[str, AnswerProvider] = {}
        self._browser_instances: dict[str, BrowserProvider] = {}
        self._browse_instances: dict[str, BrowseProvider] = {}
        # Every session this router opened that has not been seen released, so close() can end the ones a caller
        # lost track of rather than leave them billing until the backend times them out.
        self._browser_handles: list[_HandleT] = []
        # A threading lock on both routers: the async one is as easily shared across threads, each with its own
        # loop, and nothing awaits while it is held, so it can never stall an event loop.
        self._handles_lock = threading.Lock()
        # One lock for the instance caches: benchmark() rows on worker threads never race one check-then-create.
        self._providers_lock = threading.Lock()
        # Rows the sync benchmark() gave up on whose calls still run; close() waits for them, never shutting one.
        self._stragglers = Stragglers()
        self._attach_router(router, route_tools, fail_on_router_error, url_policy, router_config, route_modes)

    def _config_for(self, key: str) -> dict[str, Any]:
        """Constructor kwargs for one backend: see :func:`~web_router.client_config.config_for`."""
        return config_for(self._config, key)

    def get_provider(self, name: str | Provider | SearchProvider | None = None) -> SearchProvider:
        """Return the named search provider, constructing and caching it on first use.

        An already-constructed provider is returned untouched and never cached,
        so this router does not take ownership of something it did not build --
        see :meth:`WebRouter.close`.
        """
        if isinstance(name, SearchProvider):
            return name

        key = provider_name(name) if name is not None else self.default_provider
        with self._providers_lock:
            if key not in self._instances:
                self._instances[key] = create_provider(key, **self._config_for(key))
            return self._instances[key]

    def get_extract_provider(self, name: str | Provider | ExtractProvider | None = None) -> ExtractProvider:
        """The extract counterpart of :meth:`get_provider`, with the same caching and ownership rules."""
        if isinstance(name, ExtractProvider):
            return name

        key = provider_name(name) if name is not None else backend_of(self.default_provider)  # a pin: its backend
        with self._providers_lock:
            if key not in self._extract_instances:
                self._extract_instances[key] = create_extract_provider(key, **self._config_for(key))
            return self._extract_instances[key]

    def get_research_provider(self, name: str | Provider | ResearchProvider | None = None) -> ResearchProvider:
        """Return the named research provider, constructing and caching it on first use.

        The research counterpart of :meth:`get_provider`, with the same ownership rule.
        """
        if isinstance(name, ResearchProvider):
            return name

        key = provider_name(name) if name is not None else backend_of(self.default_provider)
        with self._providers_lock:
            if key not in self._research_instances:
                self._research_instances[key] = create_research_provider(key, **self._config_for(key))
            return self._research_instances[key]

    def get_browser_provider(self, name: str | Provider | BrowserProvider | None = None) -> BrowserProvider:
        """Return the named browser provider, constructing and caching it on first use.

        The browser counterpart of :meth:`get_provider`, with the same ownership rule.
        """
        if isinstance(name, BrowserProvider):
            return name

        key = provider_name(name) if name is not None else backend_of(self.default_provider)
        with self._providers_lock:
            if key not in self._browser_instances:
                self._browser_instances[key] = create_browser_provider(key, **self._config_for(key))
            return self._browser_instances[key]

    def get_browse_provider(self, name: str | Provider | BrowseProvider | None = None) -> BrowseProvider:
        """Return the named browse provider, constructing and caching it on first use.

        The browse counterpart of :meth:`get_provider`, with the same ownership rule.
        """
        if isinstance(name, BrowseProvider):
            return name

        key = provider_name(name) if name is not None else backend_of(self.default_provider)
        with self._providers_lock:
            if key not in self._browse_instances:
                self._browse_instances[key] = create_browse_provider(key, **self._config_for(key))
            return self._browse_instances[key]

    def _all_instances(self) -> list[_BaseProvider]:
        return [
            *self._instances.values(),
            *self._extract_instances.values(),
            *self._live_data_instances.values(),
            *self._research_instances.values(),
            *self._answer_instances.values(),
            *self._browser_instances.values(),
            *self._browse_instances.values(),
        ]

    def __repr__(self) -> str:
        return f"<{type(self).__name__} default_provider={self.default_provider!r}>"


class WebRouter(
    _BaseRouter[BrowserHandle],
    SyncSearch,
    SyncCore,
    SyncWarmup,
    SyncAnswer,
    SyncLiveData,
    SyncBenchmark,
    SyncPlan,
    SyncBudget,
    SyncVendors,
):
    """Synchronous router.

    Example:
        >>> with WebRouter("nimble") as router:
        ...     response = router.search("who won the 2022 world cup", max_results=5, timeout=10)
        ...     router.extract(response.urls[:3], format="markdown", timeout=30)

    Build one per process and share it across threads; close it (or use it as a context manager) when done.

    ``budget=`` caps one call and :meth:`budget` several: see :mod:`web_router.routing.budget`.
    """

    def __init__(
        self,
        provider: str | Provider = DEFAULT_PROVIDER,
        *,
        provider_config: dict[str, dict[str, Any]] | None = None,
        router: RouterLike | str | None = DATA_DRIVEN_ROUTER_NAME,
        router_config: Mapping[str, Any] | None = None,
        route_tools: Collection[ToolName] | None = None,
        route_modes: bool | None = None,
        fail_on_router_error: bool = False,
        url_policy: UrlPolicy | None = None,
        data_api_credentials: DataApiCredentials = None,
        vendor_credentials: VendorCredentials = None,
        max_concurrency: int | None = None,
        hop_timeout: float | Literal[False] | None = None,
        timeout_cooldown: bool = True,
    ) -> None:
        """
        Raises:
            ConfigurationError: for a backend handed an ``async_client``, which this router, calling synchronously,
                could never use: through ``auto`` the call would quietly go to another backend instead.
        """
        super().__init__(
            provider,
            provider_config=provider_config,
            router=router,
            router_config=router_config,
            route_tools=route_tools,
            route_modes=route_modes,
            fail_on_router_error=fail_on_router_error,
            url_policy=url_policy,
            data_api_credentials=renamed_credentials(data_api_credentials, vendor_credentials),
            max_concurrency=max_concurrency,
            hop_timeout=hop_timeout,
            timeout_cooldown=timeout_cooldown,
        )
        async_only = sorted(async_client_holders(self._config))
        if async_only:
            self._close_refused_router()  # built by name above, and this router is never returned to close it
            raise ConfigurationError(MSG_SYNC_ROUTER_ASYNC_CLIENT.format(names=", ".join(map(repr, async_only))))

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
        """
        request = build_research_request(
            query,
            output_schema=output_schema,
            records=records,
            effort=effort,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider_options=provider_options,
        )
        chain = self.get_research_provider(provider)
        return self._routed(chain, request, budget, lambda r: chain.research(r, timeout=timeout))

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
    ) -> ResearchJob:
        """Start a research run and hand back the job, without waiting for it.

        The same call as :meth:`research` up to the point the backend accepts the run. What comes back
        polls with :meth:`~web_router.ResearchJob.status` and blocks with :meth:`~web_router.ResearchJob.result`,
        so a caller with something else to do can go and do it.
        """
        request = build_research_request(
            query,
            output_schema=output_schema,
            records=records,
            effort=effort,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider_options=provider_options,
        )
        return submitted("research", self.get_research_provider(provider), request, self)

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
        """
        request = build_browse_request(
            task,
            start_url=start_url,
            output_schema=output_schema,
            max_steps=max_steps,
            allowed_domains=allowed_domains,
            provider_options=provider_options,
        )
        chain = self.get_browse_provider(provider)
        return self._routed(chain, request, budget, lambda r: chain.browse(r, timeout=timeout))

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
    ) -> BrowseJob:
        """Start a browse task and hand back the job, without waiting for it.

        The same call as :meth:`browse` up to the point the backend accepts the
        task; the job polls with :meth:`~web_router.BrowseJob.status` and blocks
        with :meth:`~web_router.BrowseJob.result`.
        """
        request = build_browse_request(
            task,
            start_url=start_url,
            output_schema=output_schema,
            max_steps=max_steps,
            allowed_domains=allowed_domains,
            provider_options=provider_options,
        )
        return submitted("browse", self.get_browse_provider(provider), request, self)

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
                default is to pass a larger ``timeout`` explicitly.
        """
        request = build_browser_request(
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
        return self._track(self.get_browser_provider(provider).open(request))

    def close(self) -> None:
        """Release every browser session still open, then close every provider, and the router, built here.

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
            stack.callback(self._close_router)
            stack.callback(self._close_vendors)
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
    _BaseRouter[AsyncBrowserHandle],
    AsyncSearch,
    AsyncCore,
    AsyncWarmup,
    AsyncAnswer,
    AsyncLiveData,
    AsyncBenchmark,
    AsyncPlan,
    AsyncBudget,
    AsyncVendors,
):
    """Asynchronous router.

    Example:
        >>> async with AsyncWebRouter("nimble") as router:
        ...     response = await router.search("latest nvidia earnings")
    """

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
    ) -> ResearchResponse:
        """Research a question through ``provider`` (or the router's default), and await it.

        As on the sync router, except that waiting gives the event loop back
        rather than holding a thread -- which is what makes running several
        research questions at once reasonable here and not there.
        """
        request = build_research_request(
            query,
            output_schema=output_schema,
            records=records,
            effort=effort,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider_options=provider_options,
        )
        chain = self.get_research_provider(provider)
        return await self._arouted(chain, request, budget, lambda r: chain.aresearch(r, timeout=timeout))

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
    ) -> ResearchJob:
        """Start a research run and hand back the job, without awaiting it.

        Await :meth:`~web_router.ResearchJob.aresult` when you want the answer.
        """
        request = build_research_request(
            query,
            output_schema=output_schema,
            records=records,
            effort=effort,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider_options=provider_options,
        )
        return await asubmitted("research", self.get_research_provider(provider), request, self)

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
    ) -> BrowseResponse:
        """Hand a task to a hosted browser agent through ``provider`` (or the router's default), and await it.

        As on the sync router, except that waiting gives the event loop back.
        """
        request = build_browse_request(
            task,
            start_url=start_url,
            output_schema=output_schema,
            max_steps=max_steps,
            allowed_domains=allowed_domains,
            provider_options=provider_options,
        )
        chain = self.get_browse_provider(provider)
        return await self._arouted(chain, request, budget, lambda r: chain.abrowse(r, timeout=timeout))

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
    ) -> BrowseJob:
        """Start a browse task and hand back the job, without awaiting it.

        Await :meth:`~web_router.BrowseJob.aresult` when you want the answer.
        """
        request = build_browse_request(
            task,
            start_url=start_url,
            output_schema=output_schema,
            max_steps=max_steps,
            allowed_domains=allowed_domains,
            provider_options=provider_options,
        )
        return await asubmitted("browse", self.get_browse_provider(provider), request, self)

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
    ) -> AsyncBrowserHandle:
        """Open a browser session through ``provider`` (or the router's default).

        Awaited for the handle, like :meth:`submit_research` is for its job,
        and the handle is then an async context manager::

            async with await router.open_browser() as browser:
                page = await playwright.chromium.connect_over_cdp(browser.cdp_url)

        ``stealth`` takes the same levels, and ``timeout`` defaults, as on the
        sync router; again the only way past that default is to pass a larger
        one explicitly.
        """
        request = build_browser_request(
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
        return self._track(await self.get_browser_provider(provider).aopen(request))

    async def aclose(self) -> None:
        """Release every browser session still open, then close every provider, and the router, built here.

        The same ownership rule and ordering as :meth:`WebRouter.close`.
        """
        async with AsyncExitStack() as stack:
            stack.push_async_callback(self._aclose_router)
            stack.push_async_callback(self._aclose_vendors)

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
