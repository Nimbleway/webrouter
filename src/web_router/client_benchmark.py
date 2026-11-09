"""``benchmark()``, ``benchmark_extract()`` and ``evaluate()`` on the routers, kept apart from ``client.py``.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` take these as
mixins, so ``router.benchmark``, ``router.benchmark_extract`` and ``router.evaluate``
are where they always were.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from web_router._benchmark import Stragglers, afan_out, fan_out, members_of
from web_router.client_requests import build_extract_request, build_search_request
from web_router.constants import (
    BENCHMARK_EXCLUDED,
    BENCHMARK_EXTRACT_TIMEOUT,
    BENCHMARK_TIMEOUT,
    EVALS_CONCURRENCY,
    MSG_BENCHMARK_EXTRACT_NO_PROVIDERS,
    MSG_BENCHMARK_NO_PROVIDERS,
)
from web_router.evals.datasets import Dataset
from web_router.evals.judge import Judge
from web_router.evals.types import EvaluationReport
from web_router.providers.base import ExtractProvider, SearchProvider
from web_router.registry import (
    available_extract_providers,
    available_providers,
    configured_extract_providers,
    configured_providers,
)
from web_router.types import (
    BenchmarkReport,
    BenchmarkRun,
    ExtractBenchmarkReport,
    ExtractBenchmarkRun,
    ExtractFormat,
    Provider,
    Recency,
    ResearchEffort,
    SearchDepth,
)

if TYPE_CHECKING:
    from web_router.client import AsyncWebRouter, WebRouter


class _BenchmarkMembers:
    """What both routers share for the comparisons: which backends a comparison runs by default."""

    _config: dict[str, dict[str, Any]]

    def get_provider(self, name: str | Provider | SearchProvider | None = None) -> SearchProvider:
        raise NotImplementedError

    def get_extract_provider(self, name: str | Provider | ExtractProvider | None = None) -> ExtractProvider:
        raise NotImplementedError

    def _default_benchmark_providers(self) -> list[str]:
        """Every search backend this router can use, less the ones a comparison learns nothing from.

        The environment's keys and the keyless tier, as ``configured_providers``
        reads them, plus any search backend given a ``provider_config`` here --
        a key handed to the router is as good as one in the environment.
        """
        return self._default_members(available_providers(), configured_providers())

    def _default_extract_benchmark_providers(self) -> list[str]:
        """Every extract backend this router can use, less ``auto``: the extract twin of the search default."""
        return self._default_members(available_extract_providers(), configured_extract_providers())

    def _default_members(self, available: list[str], configured: list[str]) -> list[str]:
        """One method's default comparison: its configured backends and those keyed here, less the excluded."""
        known = set(available)
        names = set(configured) | {name for name in self._config if name in known}
        return sorted(name for name in names if name not in BENCHMARK_EXCLUDED)


class SyncBenchmark(_BenchmarkMembers):
    _stragglers: Stragglers

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
        recency: Recency | None = None,
        effort: ResearchEffort | None = None,
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
        request = build_search_request(
            query,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            recency=recency,
            effort=effort,
            provider_options=None,
        )
        members = members_of(
            providers,
            default=self._default_benchmark_providers,
            build=self.get_provider,
            none_named=MSG_BENCHMARK_NO_PROVIDERS,
        )
        runs = fan_out(
            members, lambda provider: provider.search(request), BenchmarkRun.of, timeout, stragglers=self._stragglers
        )
        return BenchmarkReport(query=query, runs=runs)

    def benchmark_extract(
        self,
        urls: str | list[str],
        *,
        providers: Sequence[str | Provider | ExtractProvider] | None = None,
        timeout: float | None = BENCHMARK_EXTRACT_TIMEOUT,
        format: ExtractFormat | None = None,
    ) -> ExtractBenchmarkReport:
        """Fetch the same URLs through several providers at once and report what each did.

        The extract twin of :meth:`benchmark`: one
        :class:`~web_router.ExtractBenchmarkRun` per provider, in the order
        named, each measured around the same :meth:`extract` call a caller
        would make. A provider that fails -- a format it cannot return, a
        missing key, a backend error, or running out of ``timeout`` -- is its
        own row's :attr:`~web_router.ExtractBenchmarkRun.error`, never the
        comparison's. A call that fetched some URLs and not others is an
        answered row whose response says which, as the call itself would.
        Measured facts only: how long each took, what came back and what did
        not. Whether the text is any good is the caller's judgment.

        Args:
            providers: The backends to compare. Unset, every extract backend
                this environment holds credentials for plus the keyless ones,
                less ``auto``, which would only repeat one of them.
            timeout: Seconds for the whole comparison, as on :meth:`benchmark`;
                longer by default, because a page is fetched live and a list
                of URLs is fanned out behind one call.
            format: Sent to every row unchanged, so the rows are comparable.
                Unset, each backend returns markdown where it can and its own
                default where it cannot; a format a backend cannot return is
                that row's refusal, never a substitution.
        """
        request = build_extract_request(urls, format=format, provider_options=None)
        members = members_of(
            providers,
            default=self._default_extract_benchmark_providers,
            build=self.get_extract_provider,
            none_named=MSG_BENCHMARK_EXTRACT_NO_PROVIDERS,
        )
        runs = fan_out(
            members,
            lambda provider: provider.extract(request),
            ExtractBenchmarkRun.of,
            timeout,
            stragglers=self._stragglers,
        )
        return ExtractBenchmarkReport(urls=request.urls, runs=runs)

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
            cast("WebRouter", self),
            dataset,
            providers=providers,
            judge=judge,
            concurrency=concurrency,
            timeout=timeout,
            max_results=max_results,
            search_depth=search_depth,
        )


class AsyncBenchmark(_BenchmarkMembers):
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
        recency: Recency | None = None,
        effort: ResearchEffort | None = None,
    ) -> BenchmarkReport:
        """Run the same search through several providers at once and report what each did.

        As on the sync router, with the rows gathered on the event loop: a row
        that runs out of ``timeout`` is cancelled rather than left running.
        """
        request = build_search_request(
            query,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            recency=recency,
            effort=effort,
            provider_options=None,
        )
        members = members_of(
            providers,
            default=self._default_benchmark_providers,
            build=self.get_provider,
            none_named=MSG_BENCHMARK_NO_PROVIDERS,
        )
        runs = await afan_out(members, lambda provider: provider.asearch(request), BenchmarkRun.of, timeout)
        return BenchmarkReport(query=query, runs=runs)

    async def benchmark_extract(
        self,
        urls: str | list[str],
        *,
        providers: Sequence[str | Provider | ExtractProvider] | None = None,
        timeout: float | None = BENCHMARK_EXTRACT_TIMEOUT,
        format: ExtractFormat | None = None,
    ) -> ExtractBenchmarkReport:
        """Fetch the same URLs through several providers at once and report what each did.

        As on the sync router, with the rows gathered on the event loop: a row
        that runs out of ``timeout`` is cancelled rather than left running.
        """
        request = build_extract_request(urls, format=format, provider_options=None)
        members = members_of(
            providers,
            default=self._default_extract_benchmark_providers,
            build=self.get_extract_provider,
            none_named=MSG_BENCHMARK_EXTRACT_NO_PROVIDERS,
        )
        runs = await afan_out(members, lambda provider: provider.aextract(request), ExtractBenchmarkRun.of, timeout)
        return ExtractBenchmarkReport(urls=request.urls, runs=runs)

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
            cast("AsyncWebRouter", self),
            dataset,
            providers=providers,
            judge=judge,
            concurrency=concurrency,
            timeout=timeout,
            max_results=max_results,
            search_depth=search_depth,
        )
