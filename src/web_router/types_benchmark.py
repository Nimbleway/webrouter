"""What ``benchmark()`` and ``benchmark_extract()`` return: one row per backend, and the report.

Kept apart so ``types.py`` stops growing. Imports ``types``, which never imports
it back: import these from here, or from :mod:`web_router`.
"""

from __future__ import annotations

import time
from collections.abc import Sized
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from web_router.types import ExtractResponse, SearchResponse

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)
_RowT = TypeVar("_RowT", bound="_BenchmarkRow[Any]")
_SelfRowT = TypeVar("_SelfRowT", bound="_BenchmarkRow[Any]")


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

    @classmethod
    def of(cls: type[_SelfRowT], provider: str, started: float | None, outcome: Any) -> _SelfRowT:
        """A row from its own start: the call's latency, not the comparison's; none for a row that never started.

        ``outcome`` is the response the call returned or the exception it
        raised -- the one place a fix to latency or error handling lands for
        both methods' rows.
        """
        latency = 0.0 if started is None else time.perf_counter() - started
        if isinstance(outcome, Exception):
            return cls(provider=provider, error=outcome, latency=latency)
        return cls(provider=provider, response=outcome, latency=latency)

    @field_serializer("error")
    def _serialize_error(self, error: Exception | None) -> str | None:
        return None if error is None else str(error)

    @model_validator(mode="after")
    def _one_outcome(self) -> _BenchmarkRow[_ResponseT]:
        # Inline, as it was in types.py, which constants.py imports.
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


class _BenchmarkRows(Generic[_RowT]):
    """The ways of reading a comparison's rows that every report shares.

    A mixin rather than a model with the field: each report declares ``runs``
    itself, after what names the comparison, so the serialized order stays
    ``query`` (or ``urls``) then ``runs``, as it was before the two reports
    shared anything. The annotation below is for the type checker alone --
    pydantic reads a base's annotations as fields, order included.
    """

    if TYPE_CHECKING:
        runs: list[_RowT]

    def __len__(self) -> int:
        return len(self.runs)

    def __getitem__(self, provider: str) -> _RowT:
        """The row for ``provider``."""
        for run in self.runs:
            if run.provider == provider:
                return run
        raise KeyError(provider)

    @property
    def succeeded(self) -> list[_RowT]:
        """The rows whose call answered."""
        return [run for run in self.runs if run.ok]

    @property
    def failed(self) -> list[_RowT]:
        """The rows whose call raised instead."""
        return [run for run in self.runs if not run.ok]


class BenchmarkRun(_BenchmarkRow[SearchResponse]):
    """One provider's row in a :class:`BenchmarkReport`: what the same query got from it, and how fast."""


class BenchmarkReport(BaseModel, _BenchmarkRows[BenchmarkRun]):
    """The same query across several providers: one :class:`BenchmarkRun` per provider, in the order asked.

    Measured facts only. Latency is measured, errors are caught and reported,
    results are counted -- there is no quality score, relevance estimate or
    price, because judging results against a task is the caller's job and any
    price table shipped here would drift. The full responses ride along so
    that judgment can be applied.
    """

    model_config = ConfigDict(extra="forbid")

    query: str
    runs: list[BenchmarkRun] = Field(default_factory=list)


class ExtractBenchmarkRun(_BenchmarkRow[ExtractResponse]):
    """One provider's row in an :class:`ExtractBenchmarkReport`: what the same URLs got from it, and how fast.

    :attr:`count` is the documents that came back. A call that fetched some of
    the URLs and not others is still an answered row -- its
    :attr:`~web_router.ExtractResponse.failures` say which -- exactly as the
    :meth:`~web_router.WebRouter.extract` call it measures stays partly
    successful; only a call where nothing came back is the row's error.
    """


class ExtractBenchmarkReport(BaseModel, _BenchmarkRows[ExtractBenchmarkRun]):
    """The same URLs across several providers: one :class:`ExtractBenchmarkRun` per provider, in the order asked.

    Measured facts only, as a :class:`BenchmarkReport` is: latency around the
    call, the documents that came back and the URLs that did not, and the full
    response for judging. Whether a page's text is complete or correct is the
    caller's judgment, not a column here.
    """

    model_config = ConfigDict(extra="forbid")

    urls: list[str]
    runs: list[ExtractBenchmarkRun] = Field(default_factory=list)
