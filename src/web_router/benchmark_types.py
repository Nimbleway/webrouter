"""The rows and reports a comparison returns, and the defaults an extract comparison takes.

:meth:`~web_router.WebRouter.benchmark` and
:meth:`~web_router.WebRouter.benchmark_extract` fan one request out across
several providers and keep one row per provider. The two rows differ only in
the response they carry, so they share a base here, as the two reports do.
Every name is also importable from :mod:`web_router.types` and the package,
and that is how the package reaches them: this module builds on the responses
in :mod:`web_router.types`, which re-exports it at its end, so it must not be
the first of the two to load.
"""

from __future__ import annotations

from collections.abc import Sized
from typing import Any, Generic, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from web_router.types import ExtractResponse, SearchResponse

BENCHMARK_EXTRACT_TIMEOUT = 120.0
"""Seconds an extract comparison may take by default, twice a search's.

A page is fetched live, rendered by some backends and queued by others, and a
list of URLs is fanned out behind one call, so the slowest row of an extract
comparison runs well past a search's. The same rule applies when it runs out:
that row is timed out, the others are unaffected.
"""

MSG_BENCHMARK_EXTRACT_NO_PROVIDERS = (
    "benchmark_extract() needs at least one provider to compare; `providers` named none."
)

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)
_RunT = TypeVar("_RunT", bound="_BenchmarkRow[Any]")


class _BenchmarkRow(BaseModel, Generic[_ResponseT]):
    """One provider's row in a comparison: what the same request got from it, and how fast.

    Exactly one of :attr:`response` and :attr:`error` is set. The error is the
    :class:`~web_router.WebRouterError` the call raised -- a refused option, a
    backend failure, or a :class:`~web_router.BenchmarkTimeoutError` -- kept as
    the exception so a caller can tell them apart, and serialized as its
    message. (Typed as ``Exception`` only because this module sits below the
    exceptions in the import graph.)

    Shared by :class:`BenchmarkRun` and :class:`ExtractBenchmarkRun`, which
    differ only in the response they carry.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    provider: str
    response: _ResponseT | None = Field(default=None, description="The normalized answer, when the call succeeded.")
    error: Exception | None = Field(default=None, description="What the call raised instead, when it did not.")
    latency: float = Field(
        ge=0.0, description="Wall-clock seconds around the public call, until it answered or was given up on."
    )

    @field_serializer("error")
    def _serialize_error(self, error: Exception | None) -> str | None:
        return None if error is None else str(error)

    @model_validator(mode="after")
    def _one_outcome(self) -> _BenchmarkRow[_ResponseT]:
        # Inline rather than in constants.py, which sits above this module in the import graph.
        if (self.response is None) == (self.error is None):
            raise ValueError("a benchmark row carries exactly one of response and error")
        return self

    @property
    def ok(self) -> bool:
        """Whether the call answered."""
        return self.error is None

    @property
    def count(self) -> int:
        """How much came back -- results for a search, documents for an extract; zero when the call did not answer."""
        # Both responses count themselves; the bound is BaseModel only so pydantic can build the base.
        return 0 if self.response is None else len(cast(Sized, self.response))


class _BenchmarkRows(BaseModel, Generic[_RunT]):
    """The rows of a comparison, in the order asked, and the ways of reading them every report shares."""

    model_config = ConfigDict(extra="forbid")

    runs: list[_RunT] = Field(default_factory=list)

    def __len__(self) -> int:
        return len(self.runs)

    def __getitem__(self, provider: str) -> _RunT:
        """The row for ``provider``."""
        for run in self.runs:
            if run.provider == provider:
                return run
        raise KeyError(provider)

    @property
    def succeeded(self) -> list[_RunT]:
        """The rows whose call answered."""
        return [run for run in self.runs if run.ok]

    @property
    def failed(self) -> list[_RunT]:
        """The rows whose call raised instead."""
        return [run for run in self.runs if not run.ok]


class BenchmarkRun(_BenchmarkRow[SearchResponse]):
    """One provider's row in a :class:`BenchmarkReport`: what the same query got from it, and how fast."""


class BenchmarkReport(_BenchmarkRows[BenchmarkRun]):
    """The same query across several providers: one :class:`BenchmarkRun` per provider, in the order asked.

    Measured facts only. Latency is measured, errors are caught and reported,
    results are counted -- there is no quality score, relevance estimate or
    price, because judging results against a task is the caller's job and any
    price table shipped here would drift. The full responses ride along so
    that judgment can be applied.
    """

    query: str


class ExtractBenchmarkRun(_BenchmarkRow[ExtractResponse]):
    """One provider's row in an :class:`ExtractBenchmarkReport`: what the same URLs got from it, and how fast.

    :attr:`count` is the documents that came back. A call that fetched some of
    the URLs and not others is still an answered row -- its
    :attr:`~web_router.ExtractResponse.failures` say which -- exactly as the
    :meth:`~web_router.WebRouter.extract` call it measures stays partly
    successful; only a call where nothing came back is the row's error.
    """


class ExtractBenchmarkReport(_BenchmarkRows[ExtractBenchmarkRun]):
    """The same URLs across several providers: one :class:`ExtractBenchmarkRun` per provider, in the order asked.

    Measured facts only, as a :class:`BenchmarkReport` is: latency around the
    call, the documents that came back and the URLs that did not, and the full
    response for judging. Whether a page's text is complete or correct is the
    caller's judgment, not a column here.
    """

    urls: list[str]
