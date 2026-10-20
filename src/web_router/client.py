"""The router facade.

:class:`WebRouter` and :class:`AsyncWebRouter` hide which API is behind a call.
Point them at a provider name and call :meth:`WebRouter.search` or
:meth:`WebRouter.extract`; swapping backends is a one-word change at
construction, or a per-call ``provider=`` override.

A name does not imply every method: most search backends do not extract, and
some extract backends do not search. Each method resolves the name against its
own registry, so asking one for a backend that only serves another reports it
as unknown and lists the ones that method does serve.

:meth:`WebRouter.research` is the third method and the one that behaves
differently: an agentic run takes minutes rather than milliseconds, so it waits
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
caller gets.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import AsyncExitStack, ExitStack, suppress
from datetime import date
from types import TracebackType
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from web_router.constants import (
    AUTO_CLIENT_OPTION,
    BENCHMARK_EXCLUDED,
    BENCHMARK_TIMEOUT,
    BROWSE_TIMEOUT,
    DEFAULT_PROVIDER,
    EVALS_CONCURRENCY,
    MSG_BENCHMARK_NO_PROVIDERS,
    MSG_ROUTE_NO_DECIDER,
    MSG_SYNC_ROUTER_ASYNC_CLIENT,
    PROVIDER_AUTO,
    RESEARCH_TIMEOUT,
    ROUTE_DEFAULT_LABELS,
    ROUTE_FALLBACK_DECIDER_ERROR,
    ROUTE_FALLBACK_LOW_CONFIDENCE,
    ROUTE_QUESTIONS,
)
from web_router.decisions.registry import create_decider
from web_router.decisions.route import Answer, Route, label_values, route_from_labels, urls_in
from web_router.evals.datasets import Dataset
from web_router.evals.judge import Judge
from web_router.evals.types import EvaluationReport
from web_router.exceptions import (
    BenchmarkTimeoutError,
    ConfigurationError,
    ProviderError,
    ProviderNotFoundError,
    WebRouterError,
)
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
)
from web_router.registry import (
    available_browse_providers,
    available_browser_providers,
    available_extract_providers,
    available_providers,
    available_research_providers,
    configured_providers,
    create_browse_provider,
    create_browser_provider,
    create_extract_provider,
    create_provider,
    create_research_provider,
)
from web_router.types import (
    BenchmarkReport,
    BenchmarkRun,
    BrowseRequest,
    BrowseResponse,
    BrowserRequest,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    Provider,
    ResearchEffort,
    ResearchRequest,
    ResearchResponse,
    SearchDepth,
    SearchRequest,
    SearchResponse,
    StealthLevel,
    Viewport,
    provider_name,
)

JsonSchema = dict[str, Any] | type[BaseModel]
"""A schema for a structured answer: JSON Schema, or the pydantic model to take one from."""

_HandleT = TypeVar("_HandleT", BrowserHandle, AsyncBrowserHandle)


class _BaseRouter(Generic[_HandleT]):
    """Provider resolution and caching shared by the sync and async routers.

    Generic only in the browser handle each one hands out, which is the one
    thing the two routers hold on a caller's behalf.
    """

    def __init__(
        self,
        provider: str | Provider = DEFAULT_PROVIDER,
        *,
        provider_config: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """
        Args:
            provider: Name of the default provider, e.g. ``"nimble"`` or
                :attr:`Provider.NIMBLE`. Omit it to let ``auto`` choose.
            provider_config: Constructor kwargs per provider, keyed by provider
                name -- ``{"tavily": {"api_key": ..., "timeout": 30}}``. This is
                the only place credentials are passed explicitly; everything
                else comes from the environment.
        """
        name = provider_name(provider)
        # Either registry is enough to name a default: a router defaulted to a
        # search-only backend still extracts through `provider=`, and the
        # method that cannot serve the default says so when it is called.
        known = (
            available_providers()
            + available_extract_providers()
            + available_research_providers()
            + available_browser_providers()
            + available_browse_providers()
        )
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
        self._research_instances: dict[str, ResearchProvider] = {}
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
        # One lock for the five instance caches: evaluate() calls benchmark()
        # from worker threads, so two rows can race the same check-then-create
        # and the loser's client would never be closed. Nothing awaits while it
        # is held.
        self._providers_lock = threading.Lock()
        # Rows the sync benchmark() gave up on whose calls still run; close()
        # waits for them so it never shuts a provider under a live search.
        self._stragglers: set[Future[Any]] = set()
        self._stragglers_lock = threading.Lock()

    def _config_for(self, key: str) -> dict[str, Any]:
        """Constructor kwargs for one backend: its ``provider_config`` entry, and for ``auto`` every other entry too.

        ``auto`` builds its own members, so it is handed the config they would
        get by name -- a key passed in code reaches Nimble the same way through
        ``auto`` as through ``provider="nimble"``.
        """
        config = dict(self._config.get(key, {}))
        if key == PROVIDER_AUTO:
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

    @staticmethod
    def _build_request(
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

    def _default_benchmark_providers(self) -> list[str]:
        """Every search backend this router can use, less the ones a comparison learns nothing from.

        The environment's keys and the keyless tier, as ``configured_providers``
        reads them, plus any search backend given a ``provider_config`` here --
        a key handed to the router is as good as one in the environment.
        """
        known = set(available_providers())
        names = set(configured_providers()) | {name for name in self._config if name in known}
        return sorted(name for name in names if name not in BENCHMARK_EXCLUDED)

    def _benchmark_members(
        self, providers: Sequence[str | Provider | SearchProvider] | None
    ) -> list[tuple[str, SearchProvider | WebRouterError]]:
        """The rows to run, as ``(row name, the provider or what stopped it)``, each name once, in the order asked.

        Resolved here, on the caller's thread and before any row starts, for
        two reasons: a backend whose extra is not installed or whose key is
        missing is that row's failure rather than the comparison's, and a
        provider built inside a worker after the router was closed would be
        cached by a router that will never close it.
        """
        members: list[tuple[str, SearchProvider | WebRouterError]] = []
        seen: set[str] = set()
        for item in self._default_benchmark_providers() if providers is None else providers:
            name = item.name if isinstance(item, SearchProvider) else provider_name(item)
            if name in seen:
                continue
            seen.add(name)
            try:
                members.append((name, self.get_provider(item)))
            except WebRouterError as exc:
                members.append((name, exc))
        if not members:
            raise ValueError(MSG_BENCHMARK_NO_PROVIDERS)
        return members

    @staticmethod
    def _benchmark_row(name: str, started: float, outcome: SearchResponse | WebRouterError) -> BenchmarkRun:
        """A row from its own start: the call's latency, not the comparison's."""
        latency = time.perf_counter() - started
        if isinstance(outcome, WebRouterError):
            return BenchmarkRun(provider=name, error=outcome, latency=latency)
        return BenchmarkRun(provider=name, response=outcome, latency=latency)

    @staticmethod
    def _build_extract_request(
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

    @staticmethod
    def _build_research_request(
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
            output_schema=_as_json_schema(output_schema),
            records=records,
            effort=effort,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider_options=provider_options or {},
        )

    @staticmethod
    def _build_browser_request(
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

    @staticmethod
    def _build_browse_request(
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
            output_schema=_as_json_schema(output_schema),
            max_steps=max_steps,
            allowed_domains=allowed_domains,
            provider_options=provider_options or {},
        )

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

    def _all_instances(
        self,
    ) -> list[SearchProvider | ExtractProvider | ResearchProvider | BrowserProvider | BrowseProvider]:
        return [
            *self._instances.values(),
            *self._extract_instances.values(),
            *self._research_instances.values(),
            *self._browser_instances.values(),
            *self._browse_instances.values(),
        ]

    def __repr__(self) -> str:
        return f"<{type(self).__name__} default_provider={self.default_provider!r}>"


def _async_client_holders(config: dict[str, dict[str, Any]]) -> set[str]:
    """Every backend ``config`` hands an ``async_client``: by name, in ``auto``'s ``members_config``, or in its chain.

    A chain is a list of entries or a mapping from method to one, and an entry
    a bare name or ``(name, kwargs)`` -- only the latter can carry a client.
    """
    auto = config.get(PROVIDER_AUTO, {})
    entries: list[tuple[str, Any]] = [*config.items(), *(auto.get("members_config") or {}).items()]
    chain = auto.get("chain") or ()
    for listed in chain.values() if isinstance(chain, Mapping) else (chain,):
        entries.extend(
            (item[0], item[1]) for item in listed or () if isinstance(item, (tuple, list)) and len(item) == 2
        )
    return {
        provider_name(name)
        for name, options in entries
        if isinstance(options, Mapping) and options.get(AUTO_CLIENT_OPTION)
    }


class WebRouter(_BaseRouter[BrowserHandle]):
    """Synchronous router.

    Example:
        >>> router = WebRouter("nimble")
        >>> response = router.search("who won the 2022 world cup", max_results=5)
        >>> response.urls
        >>> router.extract(response.urls[:3], format="markdown")
    """

    def __init__(
        self,
        provider: str | Provider = DEFAULT_PROVIDER,
        *,
        provider_config: dict[str, dict[str, Any]] | None = None,
        decider: str | Any | None = None,
    ) -> None:
        """
        Args:
            decider: Turns on :meth:`classify`. ``"jev"`` asks TypeSafe's Jev with
                ``TYPESAFE_API_KEY``; ``"openai"`` an OpenAI model with
                ``OPENAI_API_KEY``; ``"rules"`` keyword rules, keyless. A name
                given to :func:`~web_router.decisions.register_decider` works
                the same way, and any instance with ``decide`` and ``close`` --
                a configured :class:`~web_router.decisions.JevDecider`, or one
                wrapped in :class:`~web_router.decisions.ValidatedDecider` -- is
                used as given. The query text is sent to it.

        Raises:
            ConfigurationError: for a backend handed an ``async_client``, which
                this router, calling synchronously, could never use: through
                ``auto`` the call would quietly go to another backend instead;
                for a decider name nothing is registered under; or for a named
                decider with no key, such as ``decider="jev"`` with no TypeSafe key.
        """
        super().__init__(provider, provider_config=provider_config)
        async_only = sorted(_async_client_holders(self._config))
        if async_only:
            raise ConfigurationError(MSG_SYNC_ROUTER_ASYNC_CLIENT.format(names=", ".join(map(repr, async_only))))
        # Built after every check that can refuse the router, so a refused build leaves no open client behind.
        if isinstance(decider, str):
            decider = create_decider(decider)  # imported on first use: Jev needs httpx, OpenAI its SDK
            self._owns_decider = True
        else:
            self._owns_decider = False  # a decider handed in may be shared: its owner closes it
        self.decider = decider

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
    ) -> SearchResponse:
        """Search the web through ``provider`` (or the router's default)."""
        request = self._build_request(
            query,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            provider_options=provider_options,
        )
        return self.get_provider(provider).search(request)

    def benchmark(
        self,
        query: str,
        *,
        providers: Sequence[str | Provider | SearchProvider] | None = None,
        timeout: float | None = BENCHMARK_TIMEOUT,
        max_results: int | None = None,
        search_depth: SearchDepth | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        country: str | None = None,
        locale: str | None = None,
    ) -> BenchmarkReport:
        """Run the same search through several providers at once and report what each did.

        One :class:`~web_router.BenchmarkRun` per provider, in the order named,
        each measured around the same :meth:`search` call a caller would make.
        A provider that fails -- a refused option, a missing key, a backend
        error, or running out of ``timeout`` -- is its own row's
        :attr:`~web_router.BenchmarkRun.error`, never the comparison's: the
        report always comes back. It carries measured facts only; judging the
        results is the caller's job.

        Args:
            providers: The backends to compare. Unset, every search backend
                this environment holds a key for plus the keyless ones, less
                ``auto``, which would only repeat one of them.
            timeout: Seconds for the whole comparison. A row still unanswered
                is reported as :class:`~web_router.BenchmarkTimeoutError` and
                its call is left to finish on its thread -- :meth:`close`
                waits for it before shutting that provider; ``None`` waits.
        """
        request = self._build_request(
            query,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            provider_options=None,
        )
        members = self._benchmark_members(providers)
        started = time.perf_counter()

        def row(name: str, provider: SearchProvider) -> BenchmarkRun:
            own = time.perf_counter()
            try:
                outcome: SearchResponse | WebRouterError = provider.search(request)
            except WebRouterError as exc:
                outcome = exc
            return self._benchmark_row(name, own, outcome)

        # A member that could not be built is its row already, with nothing to time.
        runs = {
            name: BenchmarkRun(provider=name, error=member, latency=0.0)
            for name, member in members
            if isinstance(member, WebRouterError)
        }
        # Not a `with` block: leaving one joins every thread, which would hold
        # the report until the slowest backend answered and make the timeout
        # a fiction. A row given up on finishes on its own and is discarded;
        # its provider was built before it started, so the router closes it.
        pool = ThreadPoolExecutor(max_workers=max(1, len(members) - len(runs)))
        try:
            futures: dict[str, Future[BenchmarkRun]] = {
                name: pool.submit(row, name, member) for name, member in members if isinstance(member, SearchProvider)
            }
            wait(futures.values(), timeout=timeout)
        finally:
            pool.shutdown(wait=False)
        for name, future in futures.items():
            if future.done():
                runs[name] = future.result()
                continue
            # A row given up on reports how long it was waited for. Its call
            # keeps running, on a provider this router will close, so close()
            # waits for it -- tracked here, dropped the moment it finishes.
            runs[name] = self._benchmark_row(name, started, BenchmarkTimeoutError(name, timeout or 0.0))
            with self._stragglers_lock:
                self._stragglers.add(future)
            future.add_done_callback(self._forget_straggler)
        return BenchmarkReport(query=query, runs=[runs[name] for name, _ in members])

    def _forget_straggler(self, future: Future[Any]) -> None:
        with self._stragglers_lock:
            self._stragglers.discard(future)

    def evaluate(
        self,
        dataset: Dataset,
        *,
        providers: Sequence[str | Provider | SearchProvider] | None = None,
        judge: Judge | None = None,
        concurrency: int = EVALS_CONCURRENCY,
        timeout: float | None = BENCHMARK_TIMEOUT,
        max_results: int | None = None,
        search_depth: SearchDepth | None = None,
    ) -> EvaluationReport:
        """Measure accuracy: every question in ``dataset`` through several providers, every answer judged.

        :meth:`benchmark` per question, then for each provider's results a
        judge model synthesizes one answer and grades it against the
        dataset's gold answer with the SimpleQA grader. The report scores each
        provider -- accuracy, failure rate, latency percentiles -- and names
        the judge, because the accuracy is that model's judgment of the
        backend's retrieval and is comparable only with figures the same judge
        produced. A row that fails, at the search or at the judge, is left
        out of accuracy and counted in the failure rate; a served question the
        answer missed is a miss.

        Every question costs one search per provider and two judge calls per
        provider that answered.

        Args:
            dataset: :meth:`~web_router.evals.Dataset.simpleqa` for the
                canonical test set, or
                :meth:`~web_router.evals.Dataset.from_csv` for your own.
            judge: Defaults to :class:`~web_router.evals.OpenAIJudge`, which
                needs the ``evals`` extra and ``OPENAI_API_KEY``;
                :class:`~web_router.evals.AnthropicJudge`, on
                ``ANTHROPIC_API_KEY``, is the other built-in. A judge passed
                in is left open; one built here is closed.
            concurrency: Questions in flight at once, on threads.
            timeout: Per question, for the whole search fan-out -- as on
                :meth:`benchmark`.
            max_results: Sent to every provider unchanged, as is
                ``search_depth``: one request shape per call is what makes the
                rows comparable. To pin backends to different vendor tiers, run
                this once per depth on the same dataset.
        """
        from web_router.evals.run import evaluate

        return evaluate(
            self,
            dataset,
            providers=providers,
            judge=judge,
            concurrency=concurrency,
            timeout=timeout,
            max_results=max_results,
            search_depth=search_depth,
        )

    def extract(
        self,
        urls: str | list[str],
        *,
        provider: str | Provider | ExtractProvider | None = None,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
    ) -> ExtractResponse:
        """Fetch the contents of one or more URLs through ``provider`` (or the router's default).

        Several URLs can partly succeed: what came back is in
        :attr:`~web_router.ExtractResponse.documents` and everything that did
        not is in :attr:`~web_router.ExtractResponse.failures`. A call where
        nothing came back raises instead.
        """
        request = self._build_extract_request(urls, format=format, provider_options=provider_options)
        return self.get_extract_provider(provider).extract(request)

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
        request = self._build_research_request(
            query,
            output_schema=output_schema,
            records=records,
            effort=effort,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider_options=provider_options,
        )
        return self.get_research_provider(provider).research(request, timeout=timeout)

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
        request = self._build_research_request(
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
        request = self._build_browse_request(
            task,
            start_url=start_url,
            output_schema=output_schema,
            max_steps=max_steps,
            allowed_domains=allowed_domains,
            provider_options=provider_options,
        )
        return self.get_browse_provider(provider).browse(request, timeout=timeout)

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
        request = self._build_browse_request(
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
        request = self._build_browser_request(
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

    def classify(
        self,
        query: str,
        *,
        urls: str | list[str] | None = None,
        output_schema: JsonSchema | None = None,
    ) -> Route:
        """Decide what to run for ``query`` before spending on it: the method, and the auto chain to run it on.

        One call to the decider labels the query; a lookup table turns the
        labels into a route. Passing ``urls`` routes to extract and
        ``output_schema`` to research without asking. A decider that fails or
        times out gives today's default -- search on the default chain -- with
        :attr:`Route.fallback` saying why. Nothing is searched: run it with
        :meth:`execute`, or read it and call a method yourself.

        This is a baseline for measuring decision-model routing, not a router contract.
        """
        if self.decider is None:
            raise ConfigurationError(MSG_ROUTE_NO_DECIDER)
        given = [urls] if isinstance(urls, str) else list(urls or [])
        found = given or urls_in(query)
        chains = self._default_chains()
        labels: dict[str, Answer] = {}
        fallback: str | None = None
        latency_ms = 0.0
        if given:
            values = {**ROUTE_DEFAULT_LABELS, "tool": "extract"}
        elif output_schema is not None:
            values = dict(ROUTE_DEFAULT_LABELS)
        else:
            state = {"query": query, "asked_on": date.today().isoformat()}
            started = time.perf_counter()
            try:
                labels = self.decider.decide(state, ROUTE_QUESTIONS)
            except ProviderError:
                fallback = ROUTE_FALLBACK_DECIDER_ERROR
            latency_ms = (time.perf_counter() - started) * 1000
            values, unsure = label_values(labels)
            if unsure and fallback is None:
                fallback = ROUTE_FALLBACK_LOW_CONFIDENCE
        method, chain, reason = route_from_labels(query, values, chains, urls=found, output_schema=output_schema)
        # The raw tool label, before an unsure one took its default, so the advice can say where it leaned.
        tool = labels.get("tool")
        requested = "research" if output_schema is not None else tool.choice if tool is not None else values["tool"]
        return Route(
            query=query,
            method=method,
            chain=chain,
            urls=found,
            output_schema=output_schema,
            labels=labels,
            fallback=tuple(r for r in (fallback, reason) if r),
            requested=requested,
            latency_ms=latency_ms,
        )

    def execute(
        self, route: Route, **overrides: Any
    ) -> SearchResponse | ExtractResponse | ResearchResponse | BrowseResponse:
        """Run a :class:`Route` with this router's methods, on the route's chain. ``overrides`` go to the method."""
        # The route's own arguments are defaults: an override of the same name wins, rather than colliding.
        # A provider override is used as given: the route's own auto provider is built only when it will run.
        kwargs: dict[str, Any] = {} if "provider" in overrides else {"provider": self._routed_provider(route)}
        if route.method == "extract":
            return self.extract(**{**kwargs, "urls": route.urls, **overrides})
        if route.method == "research":
            return self.research(**{**kwargs, "query": route.query, "output_schema": route.output_schema, **overrides})
        if route.method == "browse":
            start_url = route.urls[0] if route.urls else None
            return self.browse(**{**kwargs, "task": route.query, "start_url": start_url, **overrides})
        return self.search(**{**kwargs, "query": route.query, **overrides})

    def _default_chains(self) -> dict[str, tuple[tuple[str, dict[str, Any]], ...]]:
        """Each method's auto chain as this router's own ``auto`` would resolve it, a configured ``chain`` included."""
        from web_router.providers.auto import (
            AutoBrowseProvider,
            AutoExtractProvider,
            AutoProvider,
            AutoResearchProvider,
            _members_and_chain,
        )

        config = self._config_for(PROVIDER_AUTO)
        defaults = {
            "search": AutoProvider.default_chain,
            "extract": AutoExtractProvider.default_chain,
            "research": AutoResearchProvider.default_chain,
            "browse": AutoBrowseProvider.default_chain,
        }
        return {
            method: _members_and_chain(config.get("chain"), config["members_config"], method, default)[1]
            for method, default in defaults.items()
        }

    def _routed_provider(self, route: Route) -> SearchProvider | ExtractProvider | ResearchProvider | BrowseProvider:
        """An auto provider on the route's chain, cached so close() owns it and config keys reach it."""
        # The members' options belong to the key too: the same names with other settings are another provider.
        key = f"{PROVIDER_AUTO}:{json.dumps([list(e) for e in route.chain], sort_keys=True, default=repr)}"
        config = {**self._config_for(PROVIDER_AUTO), "chain": {route.method: [list(e) for e in route.chain]}}
        table: dict[str, tuple[dict[str, Any], Any]] = {
            "extract": (self._extract_instances, create_extract_provider),
            "research": (self._research_instances, create_research_provider),
            "browse": (self._browse_instances, create_browse_provider),
        }
        cache, factory = table.get(route.method, (self._instances, create_provider))
        with self._providers_lock:
            if key not in cache:
                cache[key] = factory(PROVIDER_AUTO, **config)
            routed: SearchProvider | ExtractProvider | ResearchProvider | BrowseProvider = cache[key]
            return routed

    def close(self) -> None:
        """Release every browser session still open, then close every provider this router built.

        A provider passed in already constructed is the caller's to close; this
        router never cached it and must not shut its client down. Sessions are
        released whichever provider opened them, since this router opened them.
        A session whose release fails stays tracked, so calling this again
        retries it. A search a benchmark timed out on may still be running on
        its thread; it is waited for first -- bounded by that provider's own
        HTTP timeout -- so no client is closed under a live call.
        """
        with self._stragglers_lock:
            pending = tuple(self._stragglers)
        if pending:
            wait(pending)
        with ExitStack() as stack:
            # Callbacks run last-in, first-out: the providers are pushed first
            # so they close after the releases, which need their clients. A
            # release that fails does not stop the rest.
            for instance in self._all_instances():
                stack.callback(instance.close)
            for handle in self._open_handles():
                stack.callback(self._release_tracked, handle)
            if self._owns_decider and self.decider is not None:
                stack.callback(self.decider.close)

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


class AsyncWebRouter(_BaseRouter[AsyncBrowserHandle]):
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
    ) -> SearchResponse:
        """Search the web through ``provider`` (or the router's default)."""
        request = self._build_request(
            query,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            provider_options=provider_options,
        )
        return await self.get_provider(provider).asearch(request)

    async def benchmark(
        self,
        query: str,
        *,
        providers: Sequence[str | Provider | SearchProvider] | None = None,
        timeout: float | None = BENCHMARK_TIMEOUT,
        max_results: int | None = None,
        search_depth: SearchDepth | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        country: str | None = None,
        locale: str | None = None,
    ) -> BenchmarkReport:
        """Run the same search through several providers at once and report what each did.

        As on the sync router, with the rows gathered on the event loop: a row
        that runs out of ``timeout`` is cancelled rather than left running.
        """
        request = self._build_request(
            query,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            provider_options=None,
        )
        members = self._benchmark_members(providers)
        deadline = None if timeout is None else time.perf_counter() + timeout

        async def row(name: str, member: SearchProvider | WebRouterError) -> BenchmarkRun:
            own = time.perf_counter()
            if isinstance(member, WebRouterError):
                return self._benchmark_row(name, own, member)
            # One deadline for the whole comparison: a row that starts late
            # gets what is left of it, not a fresh timeout of its own.
            remaining = None if deadline is None else max(0.0, deadline - own)
            try:
                outcome: SearchResponse | WebRouterError = await asyncio.wait_for(member.asearch(request), remaining)
            except asyncio.TimeoutError:
                outcome = BenchmarkTimeoutError(name, timeout or 0.0)
            except WebRouterError as exc:
                outcome = exc
            return self._benchmark_row(name, own, outcome)

        runs = await asyncio.gather(*(row(name, member) for name, member in members))
        return BenchmarkReport(query=query, runs=list(runs))

    async def evaluate(
        self,
        dataset: Dataset,
        *,
        providers: Sequence[str | Provider | SearchProvider] | None = None,
        judge: Judge | None = None,
        concurrency: int = EVALS_CONCURRENCY,
        timeout: float | None = BENCHMARK_TIMEOUT,
        max_results: int | None = None,
        search_depth: SearchDepth | None = None,
    ) -> EvaluationReport:
        """Measure accuracy across providers, as on the sync router, with the questions gathered on the event loop."""
        from web_router.evals.run import aevaluate

        return await aevaluate(
            self,
            dataset,
            providers=providers,
            judge=judge,
            concurrency=concurrency,
            timeout=timeout,
            max_results=max_results,
            search_depth=search_depth,
        )

    async def extract(
        self,
        urls: str | list[str],
        *,
        provider: str | Provider | ExtractProvider | None = None,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
    ) -> ExtractResponse:
        """Fetch the contents of one or more URLs through ``provider`` (or the router's default).

        Backends that take one URL per call are fanned out concurrently. As on
        the sync router, a partly successful call returns its failures and one
        where nothing came back raises.
        """
        request = self._build_extract_request(urls, format=format, provider_options=provider_options)
        return await self.get_extract_provider(provider).aextract(request)

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
    ) -> ResearchResponse:
        """Research a question through ``provider`` (or the router's default), and await it.

        As on the sync router, except that waiting gives the event loop back
        rather than holding a thread -- which is what makes running several
        research questions at once reasonable here and not there.
        """
        request = self._build_research_request(
            query,
            output_schema=output_schema,
            records=records,
            effort=effort,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider_options=provider_options,
        )
        return await self.get_research_provider(provider).aresearch(request, timeout=timeout)

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
        request = self._build_research_request(
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
    ) -> BrowseResponse:
        """Hand a task to a hosted browser agent through ``provider`` (or the router's default), and await it.

        As on the sync router, except that waiting gives the event loop back.
        """
        request = self._build_browse_request(
            task,
            start_url=start_url,
            output_schema=output_schema,
            max_steps=max_steps,
            allowed_domains=allowed_domains,
            provider_options=provider_options,
        )
        return await self.get_browse_provider(provider).abrowse(request, timeout=timeout)

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
        request = self._build_browse_request(
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
        request = self._build_browser_request(
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


def _as_json_schema(schema: JsonSchema | None) -> dict[str, Any] | None:
    """Accept a pydantic model where a JSON Schema is expected.

    Callers describing a structured answer usually have a model already, and
    the request carries plain JSON Schema so it stays serializable -- so the
    conversion happens once, here, rather than at every call site.
    """
    if schema is None or isinstance(schema, dict):
        return schema
    return schema.model_json_schema()
