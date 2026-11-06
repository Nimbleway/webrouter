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

:meth:`WebRouter.open_browser` is different again: it returns no answer at all,
but a handle on a cloud browser the caller drives over CDP. The router keeps
every handle it opened and releases the ones still open when it is closed.

:meth:`WebRouter.benchmark` is not a method of its own kind but a consumer of
:meth:`WebRouter.search`: the same query fanned out across several backends,
each row measured around the public call, so what is compared is what a
caller gets. :meth:`WebRouter.benchmark_extract` is the same consumer of
:meth:`WebRouter.extract`, the same URLs fanned out the same way.
"""

from __future__ import annotations

import threading
from collections.abc import Collection, Mapping
from contextlib import AsyncExitStack, ExitStack, suppress
from types import TracebackType
from typing import Any, Generic, TypeVar

from web_router._benchmark import Stragglers
from web_router.client_answer import AsyncAnswer, SyncAnswer
from web_router.client_benchmark import AsyncBenchmark, SyncBenchmark
from web_router.client_plan import AsyncPlan, SyncPlan
from web_router.client_requests import JsonSchema as JsonSchema
from web_router.client_requests import (
    async_client_holders,
    build_browse_request,
    build_browser_request,
    build_extract_request,
    build_research_request,
    build_search_request,
)
from web_router.constants import (
    BROWSE_TIMEOUT,
    DEFAULT_PROVIDER,
    DELEGATING_PROVIDERS,
    MAX_INHERITS_FROM_AUTO,
    MSG_SYNC_ROUTER_ASYNC_CLIENT,
    PROVIDER_AUTO,
    PROVIDER_MAX,
    RESEARCH_TIMEOUT,
)
from web_router.exceptions import ConfigurationError, ProviderNotFoundError
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
from web_router.registry import (
    create_browse_provider,
    create_browser_provider,
    create_extract_provider,
    create_provider,
    create_research_provider,
    create_template_provider,
)
from web_router.registry_answer import every_provider_name
from web_router.routing import Budget, RouterLike, ToolName
from web_router.routing.routed import RouterSetup
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
    Template,
    TemplatePage,
    TemplateRequest,
    TemplateResponse,
    Viewport,
    provider_name,
)

_HandleT = TypeVar("_HandleT", BrowserHandle, AsyncBrowserHandle)


class _BaseRouter(RouterSetup, Generic[_HandleT]):
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

    def _config_for(self, key: str) -> dict[str, Any]:
        """Constructor kwargs for one backend: its ``provider_config`` entry, and every entry for a delegating one.

        Each of those builds its own members, so it is handed the config they
        would get by name -- a key passed in code reaches Nimble the same way
        through ``auto`` or ``max`` as through ``provider="nimble"``. ``max``
        races the members ``auto`` would try, so a ``chain`` or
        ``members_config`` set under ``auto`` is its own unless it sets one.
        """
        config = dict(self._config.get(key, {}))
        if key == PROVIDER_MAX:
            shared = self._config.get(PROVIDER_AUTO, {})
            for entry in MAX_INHERITS_FROM_AUTO:
                if entry in shared and entry not in config:
                    config[entry] = shared[entry]
        if key in DELEGATING_PROVIDERS:
            siblings: dict[str, dict[str, Any]] = {name: c for name, c in self._config.items() if name != key}
            # An explicit members_config is laid over the sibling entries, member by member, never in place of them.
            explicit: dict[str, dict[str, Any]] = {
                provider_name(name): dict(c) for name, c in (config.get("members_config") or {}).items()
            }
            config["members_config"] = {
                name: {**siblings.get(name, {}), **explicit.get(name, {})} for name in {*siblings, *explicit}
            }
        return config

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
        """Return the named extract provider, constructing and caching it on first use.

        The extract counterpart of :meth:`get_provider`, with the same
        ownership rule.
        """
        if isinstance(name, ExtractProvider):
            return name

        key = provider_name(name) if name is not None else self.default_provider
        with self._providers_lock:
            if key not in self._extract_instances:
                self._extract_instances[key] = create_extract_provider(key, **self._config_for(key))
            return self._extract_instances[key]

    def get_template_provider(self, name: str | Provider | TemplateProvider) -> TemplateProvider:
        """Return the named template provider, constructing and caching it on first use.

        The template counterpart of :meth:`get_provider`, with the same
        ownership rule -- but no default: a template's name belongs to one
        vendor, so the caller always says which.
        """
        if isinstance(name, TemplateProvider):
            return name

        key = provider_name(name)
        with self._providers_lock:
            if key not in self._template_instances:
                self._template_instances[key] = create_template_provider(key, **self._config_for(key))
            return self._template_instances[key]

    def get_research_provider(self, name: str | Provider | ResearchProvider | None = None) -> ResearchProvider:
        """Return the named research provider, constructing and caching it on first use.

        The research counterpart of :meth:`get_provider`, with the same
        ownership rule.
        """
        if isinstance(name, ResearchProvider):
            return name

        key = provider_name(name) if name is not None else self.default_provider
        with self._providers_lock:
            if key not in self._research_instances:
                self._research_instances[key] = create_research_provider(key, **self._config_for(key))
            return self._research_instances[key]

    def get_browser_provider(self, name: str | Provider | BrowserProvider | None = None) -> BrowserProvider:
        """Return the named browser provider, constructing and caching it on first use.

        The browser counterpart of :meth:`get_provider`, with the same
        ownership rule.
        """
        if isinstance(name, BrowserProvider):
            return name

        key = provider_name(name) if name is not None else self.default_provider
        with self._providers_lock:
            if key not in self._browser_instances:
                self._browser_instances[key] = create_browser_provider(key, **self._config_for(key))
            return self._browser_instances[key]

    def get_browse_provider(self, name: str | Provider | BrowseProvider | None = None) -> BrowseProvider:
        """Return the named browse provider, constructing and caching it on first use.

        The browse counterpart of :meth:`get_provider`, with the same
        ownership rule.
        """
        if isinstance(name, BrowseProvider):
            return name

        key = provider_name(name) if name is not None else self.default_provider
        with self._providers_lock:
            if key not in self._browse_instances:
                self._browse_instances[key] = create_browse_provider(key, **self._config_for(key))
            return self._browse_instances[key]

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


class WebRouter(_BaseRouter[BrowserHandle], SyncAnswer, SyncBenchmark, SyncPlan):
    """Synchronous router.

    Example:
        >>> router = WebRouter("nimble")
        >>> response = router.search("who won the 2022 world cup", max_results=5)
        >>> response.urls
        >>> router.extract(response.urls[:3], format="markdown")

    ``search``, ``extract``, ``answer``, ``research`` and ``browse`` take ``budget=``, a
    :class:`~web_router.Budget` (experimental): see :mod:`web_router.routing.budget` for how it holds the call.
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
    ) -> SearchResponse:
        """Search the web through ``provider`` (or the router's default)."""
        request = build_search_request(
            query,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            provider_options=provider_options,
        )
        chain = self.get_provider(provider)
        return self._routed(chain, request, budget, chain.search)

    def extract(
        self,
        urls: str | list[str],
        *,
        provider: str | Provider | ExtractProvider | None = None,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
    ) -> ExtractResponse:
        """Fetch the contents of one or more URLs through ``provider`` (or the router's default).

        Several URLs can partly succeed: what came back is in
        :attr:`~web_router.ExtractResponse.documents` and everything that did
        not is in :attr:`~web_router.ExtractResponse.failures`. A call where
        nothing came back raises instead.
        """
        request = build_extract_request(urls, format=format, provider_options=provider_options)
        chain = self.get_extract_provider(provider)
        return self._routed(chain, request, budget, chain.extract)

    def templates(
        self,
        *,
        provider: str | Provider | TemplateProvider,
        limit: int | None = None,
        offset: int | None = None,
    ) -> TemplatePage:
        """List one page of the templates ``provider`` holds.

        A template is a vendor's saved, site-specific extraction: run on one
        page of the site it was built for, it returns a record with fixed
        fields. Names, inputs and fields are the vendor's own, which is why
        ``provider`` has no default. The page says where in the catalogue it
        sits and, when the backend reports it, how many templates there are
        in all; ``limit`` and ``offset`` left unset leave the paging to the
        backend.
        """
        return self.get_template_provider(provider).templates(limit=limit, offset=offset)

    def template(
        self,
        name: str,
        *,
        provider: str | Provider | TemplateProvider,
    ) -> Template:
        """Describe one template: the inputs a run takes and the record it returns, as JSON Schema."""
        return self.get_template_provider(provider).template(name)

    def run_template(
        self,
        template: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        provider: str | Provider | TemplateProvider,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
    ) -> TemplateResponse:
        """Run one of ``provider``'s templates on one page and return its record.

        ``inputs`` are the template's own, named by its
        :attr:`~web_router.Template.input_schema` -- a URL for some
        templates, an id, a slug or a search term for others. ``format`` asks
        for the page beside the record; unset, the record comes back alone.
        A run the backend could not complete raises rather than coming back
        with an empty record, and an unknown name raises
        :class:`~web_router.TemplateNotFoundError`. One call is one page and
        one set of inputs: a backend whose templates run as jobs over many
        inputs would answer through its synchronous endpoint, under that
        endpoint's own time cap.
        """
        request = TemplateRequest(
            template=template, inputs=dict(inputs or {}), format=format, provider_options=provider_options or {}
        )
        return self.get_template_provider(provider).run(request)

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

        The same call as :meth:`research` up to the point the backend accepts
        the run. What comes back polls with
        :meth:`~web_router.ResearchJob.status` and blocks with
        :meth:`~web_router.ResearchJob.result`, so a caller with something else
        to do can go and do it.
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
        return self.get_research_provider(provider).submit(request)

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
        return self.get_browse_provider(provider).submit(request)

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


class AsyncWebRouter(_BaseRouter[AsyncBrowserHandle], AsyncAnswer, AsyncBenchmark, AsyncPlan):
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
    ) -> SearchResponse:
        """Search the web through ``provider`` (or the router's default)."""
        request = build_search_request(
            query,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            provider_options=provider_options,
        )
        chain = self.get_provider(provider)
        return await self._arouted(chain, request, budget, chain.asearch)

    async def extract(
        self,
        urls: str | list[str],
        *,
        provider: str | Provider | ExtractProvider | None = None,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
    ) -> ExtractResponse:
        """Fetch the contents of one or more URLs through ``provider`` (or the router's default).

        Backends that take one URL per call are fanned out concurrently. As on
        the sync router, a partly successful call returns its failures and one
        where nothing came back raises.
        """
        request = build_extract_request(urls, format=format, provider_options=provider_options)
        chain = self.get_extract_provider(provider)
        return await self._arouted(chain, request, budget, chain.aextract)

    async def templates(
        self,
        *,
        provider: str | Provider | TemplateProvider,
        limit: int | None = None,
        offset: int | None = None,
    ) -> TemplatePage:
        """List one page of the templates ``provider`` holds."""
        return await self.get_template_provider(provider).atemplates(limit=limit, offset=offset)

    async def template(
        self,
        name: str,
        *,
        provider: str | Provider | TemplateProvider,
    ) -> Template:
        """Describe one template: the inputs a run takes and the record it returns, as JSON Schema."""
        return await self.get_template_provider(provider).atemplate(name)

    async def run_template(
        self,
        template: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        provider: str | Provider | TemplateProvider,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
    ) -> TemplateResponse:
        """Run one of ``provider``'s templates on one page and return its record.

        As on the sync router: ``inputs`` are the template's own, and
        ``format`` asks for the page beside the record.
        """
        request = TemplateRequest(
            template=template, inputs=dict(inputs or {}), format=format, provider_options=provider_options or {}
        )
        return await self.get_template_provider(provider).arun(request)

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
        return await self.get_research_provider(provider).asubmit(request)

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
        return await self.get_browse_provider(provider).asubmit(request)

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
