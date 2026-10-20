"""The contract every provider implements.

A provider does one job: translate between web-router's normalized types and
whatever its API speaks. The invariants that make the router trustworthy --
rejecting unmapped options, wrapping backend failures -- are enforced in the
public :meth:`SearchProvider.search` and :meth:`ExtractProvider.extract`, so a
new provider gets them by construction and cannot forget them.

There is one base class per method. A vendor that serves both writes two
classes, which is what lets a backend appear under ``extract`` and not under
``search``, or the other way round.

:class:`ResearchProvider` is the odd one: a research run is a job rather than a
call. Starting one, waiting for it and reporting it belong to
:class:`JobProvider` and the :class:`Job` it hands callers to hold while it
runs, which know nothing about research; :class:`ResearchProvider` and
:class:`ResearchJob` are that core with research's types and names on it, and
:class:`BrowseProvider` and :class:`BrowseJob` are the same core again with a
hosted browser agent's.

:class:`BrowserProvider` is the other: a browser session is a resource rather
than an answer, so the class owns opening and releasing one and hands callers
a :class:`BrowserHandle` -- or an :class:`AsyncBrowserHandle` -- that releases
it on every way out of the block that holds it. The handle also drives the
session's page -- ``goto``, ``snapshot``, ``click`` and the rest -- once, over
CDP, for every backend alike; see :mod:`web_router.providers._page`.
"""

from __future__ import annotations

import abc
import asyncio
import builtins
import enum
import time
import warnings
from collections.abc import Awaitable, Callable, Iterator, Mapping
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, Any, ClassVar, Generic, NoReturn, Protocol, TypeVar

from web_router.constants import (
    BROWSE_TIMEOUT,
    BROWSER_TIMEOUT,
    EXTRACT_DEFAULT_FORMAT,
    JOB_POLL_BACKOFF,
    JOB_POLL_INTERVAL,
    JOB_POLL_MAX_INTERVAL,
    JOB_TIMEOUT,
    JOB_TIMEOUT_ERROR_SUFFIXES,
    MAX_CONCURRENT_EXTRACTS,
    MSG_BROWSE_FAILED,
    MSG_BROWSE_NOT_RESUMABLE,
    MSG_BROWSER_CO_REQUIRED,
    MSG_BROWSER_FAILED,
    MSG_BROWSER_LEAKED,
    MSG_BROWSER_NO_STATE,
    MSG_BROWSER_RELEASE_MASKED,
    MSG_BROWSER_UNKNOWN_STATE,
    MSG_EXTRACT_FAILED,
    MSG_EXTRACT_NO_DOCUMENT,
    MSG_EXTRACT_NOTHING_RETURNED,
    MSG_JOB_CANCELLED,
    MSG_JOB_FAILED,
    MSG_JOB_NO_DETAIL,
    MSG_JOB_NOT_RESUMABLE,
    MSG_JOB_RUN_FAILED,
    MSG_JOB_UNKNOWN_STATE,
    MSG_OPTION_VALUE,
    MSG_RESEARCH_FAILED,
    MSG_RESEARCH_NOT_RESUMABLE,
    MSG_SEARCH_FAILED,
    MSG_UNSUPPORTED_PROXY_COUNTRY,
    MSG_UNSUPPORTED_STEALTH,
    RESEARCH_TIMEOUT,
)
from web_router.exceptions import (
    BrowseTimeoutError,
    MissingParameterError,
    ProviderError,
    ResearchTimeoutError,
    UnsupportedFormatError,
    UnsupportedParameterError,
    WebRouterError,
)
from web_router.providers._page import AsyncPage, SyncPage
from web_router.providers._utils import job_deadline
from web_router.types import (
    BrowseProgress,
    BrowseRequest,
    BrowseResponse,
    BrowserRequest,
    BrowserSession,
    BrowserState,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    JobState,
    NormalizedRequest,
    PageFormat,
    PageLoadState,
    PageSnapshot,
    PageState,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    Screenshot,
    SearchRequest,
    SearchResponse,
    StealthLevel,
)

if TYPE_CHECKING:
    from playwright.async_api import Page as AsyncPlaywrightPage

_ResultT = TypeVar("_ResultT")
_StateT = TypeVar("_StateT", bound=str)
_RequestT = TypeVar("_RequestT", bound=NormalizedRequest)
_ResponseT = TypeVar("_ResponseT")
_ResponseT_co = TypeVar("_ResponseT_co", covariant=True)


class _BaseProvider(abc.ABC):
    """What a provider is, whichever method it serves."""

    name: ClassVar[str]

    supported_parameters: ClassVar[frozenset[str]] = frozenset()
    """Normalized options this backend maps.

    Anything outside this set is rejected rather than dropped, so a call never
    silently runs without something the caller asked for. See
    :meth:`~web_router.types.NormalizedRequest.normalized_parameters` for
    everything on offer.
    """

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        self.api_key = api_key
        self.options = kwargs

    def _reject_unmapped(self, request: NormalizedRequest) -> None:
        unmapped = request.specified_parameters() - self.supported_parameters
        if unmapped:
            raise UnsupportedParameterError(self.name, unmapped)

    def close(self) -> None:  # noqa: B027
        """Release sync resources."""

    async def aclose(self) -> None:  # noqa: B027
        """Release async resources."""

    def __repr__(self) -> str:
        # Deliberately omits api_key and options so a stray log never leaks a credential.
        return f"<{type(self).__name__} name={self.name!r}>"


class SearchProvider(_BaseProvider):
    """Base class for a search backend.

    To add one, set :attr:`name` and :attr:`supported_parameters`, then
    implement :meth:`_search` and :meth:`_asearch`. Override ``close``/``aclose``
    if the provider holds a client. See CONTRIBUTING.md for the full checklist.
    """

    # -- What subclasses implement -------------------------------------------

    @abc.abstractmethod
    def _search(self, request: SearchRequest) -> SearchResponse:
        """Run the search. The request is already validated; errors are wrapped for you."""

    @abc.abstractmethod
    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        """Async counterpart of :meth:`_search`."""

    # -- What callers use ----------------------------------------------------

    def search(self, request: SearchRequest) -> SearchResponse:
        """Validate the request, run it, and wrap any backend failure."""
        self.validate(request)
        try:
            return self._search(request)
        except WebRouterError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, MSG_SEARCH_FAILED.format(error=exc), original=exc) from exc

    async def asearch(self, request: SearchRequest) -> SearchResponse:
        """Async counterpart of :meth:`search`."""
        self.validate(request)
        try:
            return await self._asearch(request)
        except WebRouterError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, MSG_SEARCH_FAILED.format(error=exc), original=exc) from exc

    def validate(self, request: SearchRequest) -> None:
        """Raise :class:`UnsupportedParameterError` for any option this backend does not map."""
        self._reject_unmapped(request)


class ExtractProvider(_BaseProvider):
    """Base class for a URL-contents backend.

    To add one, set :attr:`name`, :attr:`supported_parameters`,
    :attr:`supported_formats` and :attr:`default_format`, then implement
    :meth:`_extract` and :meth:`_aextract`. See CONTRIBUTING.md for the full
    checklist.

    Fanning several URLs out across calls is handled here, so a backend that
    takes one URL per request behaves like one that takes them all at once: the
    documents that came back are returned, and the URLs that did not are
    reported as failures rather than aborting the ones that worked.
    """

    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset()
    """Representations this backend can return.

    A format outside this set raises :class:`UnsupportedFormatError` rather
    than coming back as a different representation under the requested name.
    """

    default_format: ClassVar[ExtractFormat]
    """What this API returns when asked for no format in particular.

    A fact about the backend, not a choice: it is what
    :attr:`~web_router.types.ExtractedDocument.format` reports when the caller
    named none, so getting it wrong mislabels the content.
    """

    batches_urls: ClassVar[bool] = False
    """Whether one call can carry every URL. Most of these APIs take exactly one."""

    min_start_interval: ClassVar[float] = 0.0
    """Seconds between the starts of two calls of one extract, for a backend limited per second.

    A cap on calls in flight is not enough there: calls that return quickly free
    their slots early, and more start in a second than the backend allows.
    """

    # -- What subclasses implement -------------------------------------------

    @abc.abstractmethod
    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        """Fetch one call's worth of URLs.

        ``urls`` is every URL the caller asked for when :attr:`batches_urls` is
        set, and a single one otherwise. The request is already validated, and
        the failure of any one call is folded into the whole for you.
        """

    @abc.abstractmethod
    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        """Async counterpart of :meth:`_extract`."""

    # -- What callers use ----------------------------------------------------

    def extract(self, request: ExtractRequest) -> ExtractResponse:
        """Validate the request, fetch every URL, and wrap any backend failure."""
        self.validate(request)
        collected = _Collected(self.name, request.urls)
        next_start = 0.0
        for urls in self._batches(request.urls):
            if self.min_start_interval:
                time.sleep(max(0.0, next_start - time.monotonic()))
                next_start = time.monotonic() + self.min_start_interval
            try:
                collected.add(urls, self._extract(request, urls))
            except ProviderError as exc:
                collected.fail(urls, exc)
            except WebRouterError:
                raise
            except Exception as exc:
                collected.fail(urls, self._failure(exc))
        return collected.resolved()

    async def aextract(self, request: ExtractRequest) -> ExtractResponse:
        """Async counterpart of :meth:`extract`.

        The calls run concurrently, up to
        :data:`~web_router.constants.MAX_CONCURRENT_EXTRACTS` at a time, their
        starts at least :attr:`min_start_interval` apart.
        """
        self.validate(request)
        batches = self._batches(request.urls)
        limit = asyncio.Semaphore(MAX_CONCURRENT_EXTRACTS)
        spacing = asyncio.Lock()
        next_start = 0.0

        async def _one(urls: list[str]) -> ExtractResponse:
            nonlocal next_start
            async with limit:
                if self.min_start_interval:
                    async with spacing:
                        await asyncio.sleep(max(0.0, next_start - time.monotonic()))
                        next_start = time.monotonic() + self.min_start_interval
                return await self._aextract(request, urls)

        outcomes = await asyncio.gather(*(_one(urls) for urls in batches), return_exceptions=True)

        collected = _Collected(self.name, request.urls)
        for urls, outcome in zip(batches, outcomes, strict=True):
            if isinstance(outcome, ExtractResponse):
                collected.add(urls, outcome)
            elif isinstance(outcome, ProviderError):
                collected.fail(urls, outcome)
            elif isinstance(outcome, WebRouterError) or not isinstance(outcome, Exception):
                raise outcome
            else:
                collected.fail(urls, self._failure(outcome))
        return collected.resolved()

    def validate(self, request: ExtractRequest) -> None:
        """Raise for any option this backend does not map, or format it cannot return."""
        self._reject_unmapped(request)
        if request.format is not None and request.format not in self.supported_formats:
            raise UnsupportedFormatError(self.name, request.format, self.supported_formats)

    # -- Helpers for subclasses ----------------------------------------------

    def format_of(self, request: ExtractRequest) -> ExtractFormat:
        """What this call will return: the format asked for, else markdown, else this API's own default.

        With no format named, every provider that can return
        :data:`~web_router.constants.EXTRACT_DEFAULT_FORMAT` (markdown) returns
        it, so the same call answers alike whichever backend serves it; one
        that cannot -- Exa returns text only -- returns its own
        :attr:`default_format`. Providers send this to their API rather than
        omitting the field when the caller named nothing, which is the one
        place the "unset means unset" rule is deliberately not applied:
        :attr:`~web_router.ExtractedDocument.format` is then true because it
        was asked for rather than true because a vendor default has not moved.
        """
        if request.format:
            return request.format
        return EXTRACT_DEFAULT_FORMAT if EXTRACT_DEFAULT_FORMAT in self.supported_formats else self.default_format

    def _batches(self, urls: list[str]) -> list[list[str]]:
        return [list(urls)] if self.batches_urls else [[url] for url in urls]

    def _failure(self, exc: BaseException) -> ProviderError:
        return ProviderError(self.name, MSG_EXTRACT_FAILED.format(error=exc), original=exc)


class _Collected:
    """One extract call's outcome, accumulated across however many API calls it took."""

    def __init__(self, provider: str, requested: list[str]) -> None:
        self.provider = provider
        self.requested = requested
        # Whoever actually answered, which is not always `provider`: `auto`
        # delegates, and the response must name the backend that served the
        # call rather than the one that chose it.
        self.served_by: str | None = None
        self.documents: list[ExtractedDocument] = []
        self.failures: list[ExtractFailure] = []
        self.payloads: list[dict[str, Any]] = []
        self.request_id: str | None = None
        self.errors: list[tuple[list[str], ProviderError]] = []

    def add(self, urls: list[str], response: ExtractResponse) -> None:
        self.served_by = self.served_by or response.provider
        self.documents.extend(response.documents)
        self.failures.extend(response.failures)
        self.payloads.extend(response.raw or [])
        self.request_id = self.request_id or response.request_id
        self._account_for(urls, response)

    def _account_for(self, urls: list[str], response: ExtractResponse) -> None:
        """Report any URL of this call the backend mentioned neither way.

        A backend can answer 200 with a payload its mapping did not expect --
        Oxylabs returns no result tagged with the format asked for, a batching
        backend leaves a URL out of its list -- and the URL would otherwise
        vanish from a call that partly succeeded. Only reached when fewer items
        came back than URLs went out, so a backend that canonicalizes the URL it
        echoes is not mistaken for one that dropped it.
        """
        if len(response.documents) + len(response.failures) >= len(urls):
            return

        mentioned = {document.url for document in response.documents} | {failure.url for failure in response.failures}
        self.failures.extend(
            ExtractFailure(url=url, error=MSG_EXTRACT_NO_DOCUMENT) for url in urls if url not in mentioned
        )

    def fail(self, urls: list[str], error: ProviderError) -> None:
        """Record a call that raised as a failure against each URL it carried."""
        self.errors.append((urls, error))
        self.failures.extend(ExtractFailure(url=url, error=str(error)) for url in urls)

    def resolved(self) -> ExtractResponse:
        # Not `if self.failures`: a backend can also answer successfully with
        # nothing in it, and an empty response is the one outcome a caller
        # cannot distinguish from a page that had no text.
        if not self.documents:
            raise self._exhausted()
        return ExtractResponse(
            provider=self.served_by or self.provider,
            documents=self._in_request_order(),
            failures=self.failures,
            request_id=self.request_id,
            raw=self.payloads or None,
        )

    def _in_request_order(self) -> list[ExtractedDocument]:
        """The documents in the order the caller asked for their URLs.

        The fan-out answers in order already, but a backend taking every URL at
        once answers in its own -- so without this, which backend served the
        call would be visible in the order of the result. Keyed on the URL as it
        was asked for: a backend that canonicalizes what it echoes matches
        nothing here and keeps its arrival order, which for a one-URL-per-call
        backend is the caller's order anyway.
        """
        position = {url: index for index, url in enumerate(self.requested)}
        if not all(document.url in position for document in self.documents):
            return self.documents
        return sorted(self.documents, key=lambda document: position[document.url])

    def _exhausted(self) -> ProviderError:
        """Say why nothing came back.

        When everything that failed came from a single call, that call's own
        error is raised untouched -- so extracting one URL through a backend
        with a bad credential reports the 401, not a summary of one.
        """
        if len(self.errors) == 1:
            urls, error = self.errors[0]
            if len(self.failures) == len(urls):
                return error

        failures = self.failures or [ExtractFailure(url=url, error=MSG_EXTRACT_NO_DOCUMENT) for url in self.requested]
        detail = "; ".join(f"{failure.url}: {failure.error}" for failure in failures)
        original = self.errors[0][1].original if self.errors else None
        return ProviderError(self.provider, MSG_EXTRACT_NOTHING_RETURNED.format(failures=detail), original=original)


class _Default(enum.Enum):
    """Marks an argument the caller did not pass, where ``None`` already means something."""

    TIMEOUT = enum.auto()


class JobProgress(Protocol[_ResponseT_co]):
    """What the job core reads off a progress report, whichever method it belongs to.

    Each method keeps its own progress model -- :class:`~web_router.ResearchProgress`
    is the shape -- so its response is typed and documented as that method's
    own, and the core needs only these five things from it.
    """

    @property
    def job_id(self) -> str: ...

    @property
    def state(self) -> JobState: ...

    @property
    def response(self) -> _ResponseT_co | None: ...

    @property
    def detail(self) -> str | None: ...

    @property
    def is_terminal(self) -> bool: ...


_ProgressT = TypeVar("_ProgressT", bound=JobProgress[Any])


class JobProvider(_BaseProvider, Generic[_RequestT, _ProgressT, _ResponseT]):
    """Base class for a method that is a *job* rather than a call.

    The backend accepts the work, carries on with it for somewhere between a
    minute and an hour, and answers when it is done. That shape is owned here
    -- starting a run, backing off while it is polled, timing out, cancelling,
    and turning a finished run into an answer or the reason there is none -- so
    a provider implements only how its API starts a run and how it reports one:
    :meth:`_start` and :meth:`_fetch`, plus their async counterparts.

    A method built on this -- :class:`ResearchProvider` is one -- names its
    request, progress and response types, implements :meth:`submit` and
    :meth:`asubmit` to hand back its own :class:`Job`, says in
    :meth:`_timed_out` what giving up on a run raises, and may put its own
    words on the two messages below.

    A backend that answers inside the request that started it implements
    :meth:`_start` alone, returns a ``completed`` progress from it, and sets
    :attr:`resumable` to ``False``.

    One thing the types do not hold: ``_ProgressT`` is bound to
    ``JobProgress[Any]``, so mypy does not check that a progress's
    ``response`` is a ``_ResponseT``. A method whose progress model carries
    some other response type type-checks anyway, and it is the method's
    progress model -- :class:`~web_router.ResearchProgress` typing ``response``
    as :class:`~web_router.ResearchResponse` -- that keeps the two in step.
    """

    resumable: ClassVar[bool] = True
    """Whether a run can be polled after the call that started it returned.

    ``False`` for a backend that does the whole run inside one request, which
    has no id to come back to. Waiting for the answer works either way; only
    handing the job around afterwards needs this.
    """

    _failed_message: ClassVar[str] = MSG_JOB_FAILED
    """How a wrapped backend failure reads, with the original as ``{error}``.

    Defaulted rather than required, so a method that forgets to name its own
    still reports the backend's failure instead of an ``AttributeError`` from
    inside the error path.
    """

    _not_resumable_message: ClassVar[str] = MSG_JOB_NOT_RESUMABLE
    """Why a run that cannot be resumed cannot be polled, naming the ``{provider}``."""

    # -- What subclasses implement -------------------------------------------

    @abc.abstractmethod
    def _start(self, request: _RequestT) -> _ProgressT:
        """Start the run and report where it is.

        Return a progress carrying the backend's own job id. A backend that
        answers immediately returns ``state="completed"`` with the response
        attached, and is never polled.
        """

    @abc.abstractmethod
    async def _astart(self, request: _RequestT) -> _ProgressT:
        """Async counterpart of :meth:`_start`."""

    def _fetch(self, request: _RequestT, job_id: str) -> _ProgressT:
        """Report where a started run is now, with the answer once it has one.

        The default refuses, which is right for a backend that has no run to
        come back to. Any backend whose :attr:`resumable` is ``True`` overrides
        this.
        """
        raise ProviderError(self.name, self._not_resumable_message.format(provider=self.name))

    async def _afetch(self, request: _RequestT, job_id: str) -> _ProgressT:
        """Async counterpart of :meth:`_fetch`."""
        return self._fetch(request, job_id)

    def _cancel(self, job_id: str) -> bool:
        """Stop a running job. ``False`` when this backend cannot, which is the default."""
        return False

    async def _acancel(self, job_id: str) -> bool:
        """Async counterpart of :meth:`_cancel`."""
        return self._cancel(job_id)

    # -- What each method implements -----------------------------------------

    @abc.abstractmethod
    def submit(self, request: _RequestT) -> Job[_RequestT, _ProgressT, _ResponseT]:
        """Start the run through :meth:`_begin` and hand back this method's own job."""

    @abc.abstractmethod
    async def asubmit(self, request: _RequestT) -> Job[_RequestT, _ProgressT, _ResponseT]:
        """Async counterpart of :meth:`submit`, through :meth:`_abegin`."""

    @abc.abstractmethod
    def _timed_out(self, job_id: str, timeout: float) -> WebRouterError:
        """The error that says a run outlived the caller's wait, and how to pick it back up."""

    # -- What callers use ----------------------------------------------------

    def poll(self, request: _RequestT, job_id: str) -> _ProgressT:
        """Ask the backend where a run is, wrapping any failure."""
        return self._guarded(lambda: self._fetch(request, job_id))

    async def apoll(self, request: _RequestT, job_id: str) -> _ProgressT:
        """Async counterpart of :meth:`poll`."""
        return await self._aguarded(self._afetch(request, job_id))

    def cancel(self, job_id: str) -> bool:
        """Ask the backend to stop a run, wrapping any failure."""
        return self._guarded(lambda: self._cancel(job_id))

    async def acancel(self, job_id: str) -> bool:
        """Async counterpart of :meth:`cancel`."""
        return await self._aguarded(self._acancel(job_id))

    def validate(self, request: _RequestT) -> None:
        """Raise :class:`UnsupportedParameterError` for any option this backend does not map."""
        self._reject_unmapped(request)

    # -- Helpers for subclasses ----------------------------------------------

    def state_of(self, reported: str, states: Mapping[str, JobState]) -> JobState:
        """Translate a backend's own run status through its table.

        A status the table does not list raises rather than being passed
        through: an unrecognized state is a backend that has changed under us,
        and treating it as "still running" would poll a finished job forever.
        """
        return _translate_state(self.name, reported, states, MSG_JOB_UNKNOWN_STATE)

    def _begin(self, request: _RequestT) -> _ProgressT:
        """Validate the request and start the run, for :meth:`submit` to wrap in a job."""
        self.validate(request)
        return self._guarded(lambda: self._start(request))

    async def _abegin(self, request: _RequestT) -> _ProgressT:
        """Async counterpart of :meth:`_begin`."""
        self.validate(request)
        return await self._aguarded(self._astart(request))

    def _run(self, request: _RequestT, timeout: float | None) -> _ResponseT:
        """Submit the run and wait up to ``timeout`` for its answer."""
        deadline = _deadline_for(timeout)
        with job_deadline(deadline):
            # The budget starts here, not inside result(): a backend that does
            # the whole run inside the call that starts it would otherwise spend
            # its own HTTP timeout before the caller's had begun.
            return self.submit(request)._wait_until(deadline, timeout)

    async def _arun(self, request: _RequestT, timeout: float | None) -> _ResponseT:
        """Async counterpart of :meth:`_run`."""
        deadline = _deadline_for(timeout)
        with job_deadline(deadline):
            job = await self.asubmit(request)
            return await job._await_until(deadline, timeout)

    # -- Helpers -------------------------------------------------------------

    def _guarded(self, call: Callable[[], _ResultT]) -> _ResultT:
        try:
            return call()
        except WebRouterError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, self._failed_message.format(error=exc), original=exc) from exc

    async def _aguarded(self, awaitable: Awaitable[_ResultT]) -> _ResultT:
        try:
            return await awaitable
        except WebRouterError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, self._failed_message.format(error=exc), original=exc) from exc


class Job(Generic[_RequestT, _ProgressT, _ResponseT]):
    """A run in progress, and the handle you wait on.

    Polling backs off as the run goes on, so a job that takes an hour is not
    asked about two thousand times, and :meth:`result` can be called more than
    once -- the answer is kept once it arrives rather than re-fetched. Each
    method subclasses this for its own name, and sets the class attributes
    below where its runs call for a different wait: :class:`ResearchJob` is
    one.
    """

    default_timeout: ClassVar[float | None] = JOB_TIMEOUT
    """Seconds :meth:`result` waits when the caller names no ``timeout``. ``None`` waits as long as the run takes."""

    poll_interval: ClassVar[float] = JOB_POLL_INTERVAL
    """Seconds before the first poll of a run that is still going."""

    poll_backoff: ClassVar[float] = JOB_POLL_BACKOFF
    """What the gap between polls is multiplied by after each one."""

    poll_max_interval: ClassVar[float] = JOB_POLL_MAX_INTERVAL
    """The ceiling that gap backs off to and stays at."""

    def __init__(
        self,
        provider: JobProvider[_RequestT, _ProgressT, _ResponseT],
        request: _RequestT,
        progress: _ProgressT,
    ) -> None:
        self._provider = provider
        self._request = request
        self._progress = progress

    @property
    def job_id(self) -> str:
        """The backend's own id for this run."""
        return self._progress.job_id

    @property
    def provider(self) -> str:
        """The backend running it."""
        return self._provider.name

    @property
    def state(self) -> JobState:
        """Where the run was the last time anyone looked, without asking again."""
        return self._progress.state

    @property
    def progress(self) -> _ProgressT:
        """The last progress anyone saw, without asking the backend again."""
        return self._progress

    @property
    def resumable(self) -> bool:
        """Whether this run can be polled at all, or only waited out in one call."""
        return self._provider.resumable

    def status(self) -> _ProgressT:
        """Ask the backend where the run is now.

        A run that has already finished is not asked about again: its last
        progress is returned as it stands.
        """
        if not self._progress.is_terminal:
            self._progress = self._provider.poll(self._request, self.job_id)
        return self._progress

    async def astatus(self) -> _ProgressT:
        """Async counterpart of :meth:`status`."""
        if not self._progress.is_terminal:
            self._progress = await self._provider.apoll(self._request, self.job_id)
        return self._progress

    def result(self, *, timeout: float | _Default | None = _Default.TIMEOUT) -> _ResponseT:
        """Wait for the run to finish and return its answer.

        Args:
            timeout: Seconds to wait; :attr:`default_timeout` when not given, and
                ``None`` to wait as long as the run takes.

        Raises:
            WebRouterError: whatever the method's :meth:`JobProvider._timed_out`
                says, if ``timeout`` elapses first. The run is left alone; call
                this again to keep waiting.
            ProviderError: if the run failed or was cancelled.
        """
        wait = self._timeout(timeout)
        deadline = _deadline_for(wait)
        with job_deadline(deadline):
            return self._wait_until(deadline, wait)

    async def aresult(self, *, timeout: float | _Default | None = _Default.TIMEOUT) -> _ResponseT:
        """Async counterpart of :meth:`result`."""
        wait = self._timeout(timeout)
        deadline = _deadline_for(wait)
        with job_deadline(deadline):
            return await self._await_until(deadline, wait)

    def _timeout(self, timeout: float | _Default | None) -> float | None:
        # A sentinel rather than a default of `default_timeout` itself, which
        # would be read once when this class was defined and never again, so a
        # method's own setting would not reach it.
        return self.default_timeout if timeout is _Default.TIMEOUT else timeout

    def _wait_until(self, deadline: float | None, timeout: float | None) -> _ResponseT:
        """Poll until the run finishes or ``deadline`` passes.

        The deadline is checked before each poll as well as before each sleep.
        A run that finished while nobody was looking -- which is every run a
        synchronous backend serves -- is answered even when the budget is
        already spent: it has been paid for, and throwing the answer away to
        report a timeout would be the one outcome nobody wants.

        A poll whose request timed out once the deadline has passed raises the
        method's timeout, with the failure as its cause; any other failure, an
        auth error or an unrecognized status, is raised as it is. Every call
        inside the run is clamped to what the caller has left, so a status
        request that stalls fails exactly as the deadline arrives; raised as the
        backend's read timeout, it would lose the job id the caller needs to
        come back to a run that is still going, and billing.
        """
        for delay in self._poll_delays():
            try:
                progress = self.status()
            except ProviderError as exc:
                self._expire(exc, deadline, timeout)
            if progress.is_terminal:
                return self._answer(progress)
            time.sleep(self._wait(delay, deadline, timeout))
        raise AssertionError  # pragma: no cover -- _poll_delays never ends

    async def _await_until(self, deadline: float | None, timeout: float | None) -> _ResponseT:
        """Async counterpart of :meth:`_wait_until`."""
        for delay in self._poll_delays():
            try:
                progress = await self.astatus()
            except ProviderError as exc:
                self._expire(exc, deadline, timeout)
            if progress.is_terminal:
                return self._answer(progress)
            await asyncio.sleep(self._wait(delay, deadline, timeout))
        raise AssertionError  # pragma: no cover -- _poll_delays never ends

    def _expire(self, exc: ProviderError, deadline: float | None, timeout: float | None) -> NoReturn:
        """Raise the method's timeout for a poll that timed out past the deadline, else ``exc`` as it is."""
        if _passed(deadline) and _is_timeout(exc):
            raise self._provider._timed_out(self.job_id, timeout or 0.0) from exc
        raise exc

    def _poll_delays(self) -> Iterator[float]:
        """The gap before each poll, backing off to a ceiling and staying there."""
        delay = self.poll_interval
        while True:
            yield delay
            delay = min(delay * self.poll_backoff, self.poll_max_interval)

    def cancel(self) -> bool:
        """Ask the backend to stop the run. ``False`` if it cannot, or if it already stopped."""
        if self._progress.is_terminal:
            return False
        return self._provider.cancel(self.job_id)

    async def acancel(self) -> bool:
        """Async counterpart of :meth:`cancel`."""
        if self._progress.is_terminal:
            return False
        return await self._provider.acancel(self.job_id)

    def _wait(self, delay: float, deadline: float | None, timeout: float | None) -> float:
        """How long to sleep before the next poll, or raise if there is no time left."""
        if deadline is None:
            return delay
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise self._provider._timed_out(self.job_id, timeout or 0.0)
        return min(delay, remaining)

    def _answer(self, progress: _ProgressT) -> _ResponseT:
        """The finished run's answer, or the reason there is none."""
        response: _ResponseT | None = progress.response
        if progress.state == "cancelled":
            raise ProviderError(self.provider, MSG_JOB_CANCELLED)
        if progress.state == "failed" or response is None:
            detail = progress.detail or MSG_JOB_NO_DETAIL
            raise ProviderError(self.provider, MSG_JOB_RUN_FAILED.format(detail=detail))
        return response

    def __repr__(self) -> str:
        return f"<{type(self).__name__} provider={self.provider!r} job_id={self.job_id!r} state={self.state!r}>"


class ResearchProvider(JobProvider[ResearchRequest, ResearchProgress, ResearchResponse]):
    """Base class for an agentic research backend.

    Research is a *job* rather than a call: the backend accepts the question,
    works on it for somewhere between a minute and an hour, and answers when it
    is done. That shape is owned by :class:`JobProvider`, so a provider
    implements only how its API starts a run and how it reports one --
    :meth:`_start` and :meth:`_fetch`, plus their async counterparts -- and
    every backend then waits, times out and reports the same way.

    A backend that answers inside the request that started it -- Perplexity's
    deep research model is one -- implements :meth:`_start` alone, returns a
    ``completed`` progress from it, and sets :attr:`resumable` to ``False``.
    """

    _failed_message = MSG_RESEARCH_FAILED
    _not_resumable_message = MSG_RESEARCH_NOT_RESUMABLE

    # -- What callers use ----------------------------------------------------

    def submit(self, request: ResearchRequest) -> ResearchJob:
        """Validate the request, start the run, and hand back a job to wait on."""
        return ResearchJob(self, request, self._begin(request))

    async def asubmit(self, request: ResearchRequest) -> ResearchJob:
        """Async counterpart of :meth:`submit`."""
        return ResearchJob(self, request, await self._abegin(request))

    def research(self, request: ResearchRequest, *, timeout: float | None = RESEARCH_TIMEOUT) -> ResearchResponse:
        """Run the research and wait for the answer.

        Args:
            request: The normalized request.
            timeout: Seconds to wait before giving up. ``None`` waits as long as
                the run takes. Giving up raises
                :class:`~web_router.ResearchTimeoutError` and leaves the run
                going -- it does not cancel it.
        """
        return self._run(request, timeout)

    async def aresearch(
        self, request: ResearchRequest, *, timeout: float | None = RESEARCH_TIMEOUT
    ) -> ResearchResponse:
        """Async counterpart of :meth:`research`."""
        return await self._arun(request, timeout)

    def validate(self, request: ResearchRequest) -> None:
        """Raise :class:`UnsupportedParameterError` for any option this backend does not map.

        ``output_schema`` and ``records`` are ordinary options here rather than
        special cases: a backend with no structured output simply does not
        declare ``output_schema``, and asking it for one says so in the same
        sentence a backend that cannot geo-target a search does.
        """
        self._reject_unmapped(request)

    # -- Helpers -------------------------------------------------------------

    def _timed_out(self, job_id: str, timeout: float) -> WebRouterError:
        return ResearchTimeoutError(self.name, job_id, timeout)


class ResearchJob(Job[ResearchRequest, ResearchProgress, ResearchResponse]):
    """A research run in progress, and the handle you wait on.

    Returned by :meth:`WebRouter.submit_research`. Polling backs off as the run
    goes on, so a job that takes an hour is not asked about two thousand times,
    and :meth:`result` can be called more than once -- the answer is kept once
    it arrives rather than re-fetched.
    """

    default_timeout = RESEARCH_TIMEOUT

    # Restated rather than inherited so the public signature still shows
    # RESEARCH_TIMEOUT as the default, as it did before the core existed. The
    # annotation keeps Job's sentinel only because an override may not narrow it.
    def result(self, *, timeout: float | _Default | None = RESEARCH_TIMEOUT) -> ResearchResponse:
        """Wait for the run to finish and return its answer.

        Raises:
            ResearchTimeoutError: if ``timeout`` elapses first. The run is left
                alone; call this again to keep waiting.
            ProviderError: if the run failed or was cancelled.
        """
        return super().result(timeout=timeout)

    async def aresult(self, *, timeout: float | _Default | None = RESEARCH_TIMEOUT) -> ResearchResponse:
        """Async counterpart of :meth:`result`."""
        return await super().aresult(timeout=timeout)


class BrowseProvider(JobProvider[BrowseRequest, BrowseProgress, BrowseResponse]):
    """Base class for a hosted browser-agent backend.

    A browse task is research with a browser instead of a search index: the
    vendor's agent drives one of its own browsers through the task and answers
    when it is done, minutes later. It is a job, so :class:`JobProvider` owns
    the waiting, and a provider implements only how its API starts a task and
    how it reports one -- :meth:`_start` and :meth:`_fetch`, their async
    counterparts, and :meth:`_cancel` where the API can stop one.

    A backend whose task call blocks until the agent is done implements
    :meth:`_start` alone, returns a ``completed`` progress from it, and sets
    :attr:`resumable` to ``False``.
    """

    required_parameters: ClassVar[frozenset[str]] = frozenset()
    """Options this backend cannot start a task without.

    Each must also be in :attr:`supported_parameters`. A request that leaves one
    unset raises :class:`~web_router.MissingParameterError` before anything is
    billed -- ``start_url`` for a backend that cannot find a first page itself.
    """

    _failed_message = MSG_BROWSE_FAILED
    _not_resumable_message = MSG_BROWSE_NOT_RESUMABLE

    # -- What callers use ----------------------------------------------------

    def submit(self, request: BrowseRequest) -> BrowseJob:
        """Validate the request, start the task, and hand back a job to wait on."""
        return BrowseJob(self, request, self._begin(request))

    async def asubmit(self, request: BrowseRequest) -> BrowseJob:
        """Async counterpart of :meth:`submit`."""
        return BrowseJob(self, request, await self._abegin(request))

    def browse(self, request: BrowseRequest, *, timeout: float | None = BROWSE_TIMEOUT) -> BrowseResponse:
        """Run the task and wait for the answer.

        Args:
            request: The normalized request.
            timeout: Seconds to wait before giving up. ``None`` waits as long as
                the task takes. Giving up raises
                :class:`~web_router.BrowseTimeoutError` and leaves the task
                going -- it does not cancel it.
        """
        return self._run(request, timeout)

    async def abrowse(self, request: BrowseRequest, *, timeout: float | None = BROWSE_TIMEOUT) -> BrowseResponse:
        """Async counterpart of :meth:`browse`."""
        return await self._arun(request, timeout)

    def validate(self, request: BrowseRequest) -> None:
        """Raise for any option this backend does not map, or any it needs and did not get.

        ``output_schema`` is an ordinary option, as it is for research: a
        backend with no structured output does not declare it, and asking it for
        one raises :class:`UnsupportedParameterError` rather than returning
        prose under the schema's name.
        """
        self._reject_unmapped(request)
        missing = self.missing_parameters(request)
        if missing:
            raise MissingParameterError(self.name, missing)

    @classmethod
    def missing_parameters(cls, request: BrowseRequest) -> frozenset[str]:
        """The :attr:`required_parameters` this request leaves unset.

        On the class, so a chooser can rule a backend out before constructing
        it -- which costs no credential -- through the same check
        :meth:`validate` makes.
        """
        return frozenset(name for name in cls.required_parameters if getattr(request, name) is None)

    # -- Helpers -------------------------------------------------------------

    def _timed_out(self, job_id: str, timeout: float) -> WebRouterError:
        return BrowseTimeoutError(self.name, job_id, timeout)


class BrowseJob(Job[BrowseRequest, BrowseProgress, BrowseResponse]):
    """A browse task in progress, and the handle you wait on.

    Returned by :meth:`WebRouter.submit_browse`, and polled, waited on and
    cancelled exactly as a :class:`ResearchJob` is.
    """

    default_timeout = BROWSE_TIMEOUT

    # Restated rather than inherited so the public signature shows
    # BROWSE_TIMEOUT as the default instead of Job's sentinel, as ResearchJob does.
    def result(self, *, timeout: float | _Default | None = BROWSE_TIMEOUT) -> BrowseResponse:
        """Wait for the task to finish and return its answer.

        Raises:
            BrowseTimeoutError: if ``timeout`` elapses first. The task is left
                alone; call this again to keep waiting.
            ProviderError: if the task failed or was cancelled.
        """
        return super().result(timeout=timeout)

    async def aresult(self, *, timeout: float | _Default | None = BROWSE_TIMEOUT) -> BrowseResponse:
        """Async counterpart of :meth:`result`."""
        return await super().aresult(timeout=timeout)


class BrowserProvider(_BaseProvider):
    """Base class for a browser session backend, cloud or local.

    A session is a resource rather than an answer: the backend starts a
    browser, hands back where to connect to it, and bills until somebody ends
    it. Opening and ending are owned here, so a provider implements only how
    its API does each -- :meth:`_open` and :meth:`_release`, plus their async
    counterparts -- and every backend then validates, reports failures and
    releases the same way. :meth:`_fetch` is optional; a backend that cannot
    say where a session is leaves it alone.

    Driving the browser is not part of a provider's job either. The handle's
    page actions run over
    :attr:`BrowserSession.cdp_url <web_router.BrowserSession.cdp_url>`, as does
    Playwright, browser-use or Stagehand connected to it by the caller, and the
    backend is out of the picture until the session is released.
    """

    release_ended_states: ClassVar[frozenset[BrowserState]] = frozenset({"released", "expired", "failed"})
    """The states in which a failed release still leaves the caller what they wanted: a session that bills no more.

    ``failed`` is in by default, because on most backends it is a session that
    crashed or never started. A backend whose ``failed`` also covers a stop
    that went wrong -- and so a session that may still bill -- leaves it out.
    """

    stealth_levels: ClassVar[frozenset[StealthLevel]] = frozenset()
    """The stealth levels this backend can serve: derive it from the level table, ``frozenset(ACME_STEALTH)``.

    Non-empty exactly when ``stealth`` is in :attr:`supported_parameters`. A
    level outside it raises :class:`UnsupportedParameterError` naming the
    level, and is never served as another one.
    """

    proxy_countries: ClassVar[frozenset[str] | None] = None
    """The countries this backend has a proxy in, as upper-case ISO 3166-1 alpha-2 codes, when it publishes a list.

    A code outside it raises :class:`UnsupportedParameterError` naming it,
    as a stealth level outside :attr:`stealth_levels` does, before anything
    opens and matched whatever its case. ``None`` sends any code and leaves the
    backend to refuse one it lacks.
    """

    co_requirements: ClassVar[Mapping[str, Mapping[Any, frozenset[str]]]] = MappingProxyType({})
    """Options this backend serves only beside others: option -> value -> the options that must be set with it.

    Anchor turns its extra stealth off unless its proxy is on, so a session
    opened with one and not the other would silently lack what was asked for.
    A request that sets an option to a listed value and leaves one of its
    partners unset raises :class:`MissingParameterError` before anything is
    billed. Every option named, on either side, must be in
    :attr:`supported_parameters`.
    """

    # -- What subclasses implement -------------------------------------------

    @abc.abstractmethod
    def _open(self, request: BrowserRequest) -> BrowserSession:
        """Start a session and describe it.

        The request is already validated, and carries
        :data:`~web_router.constants.BROWSER_TIMEOUT` as its ``timeout`` when the
        caller set none and this backend maps one. Errors are wrapped for you.
        """

    @abc.abstractmethod
    async def _aopen(self, request: BrowserRequest) -> BrowserSession:
        """Async counterpart of :meth:`_open`."""

    @abc.abstractmethod
    def _release(self, session_id: str) -> None:
        """End a session.

        May raise for a session the backend has already ended -- :meth:`release`
        checks with :meth:`_fetch` before reporting that as a failure.
        """

    @abc.abstractmethod
    async def _arelease(self, session_id: str) -> None:
        """Async counterpart of :meth:`_release`."""

    def _fetch(self, session_id: str) -> BrowserState:
        """Report where a session is now, through :meth:`state_of`.

        The default refuses, which is right for a backend with no way to ask.
        """
        raise ProviderError(self.name, MSG_BROWSER_NO_STATE.format(provider=self.name))

    async def _afetch(self, session_id: str) -> BrowserState:
        """Async counterpart of :meth:`_fetch`."""
        return self._fetch(session_id)

    def _control_url(self, session_id: str) -> str | None:
        """Ask the backend for a page where a person can drive the session, when it hands one out only on request.

        The default asks nothing, which is right for a backend that offers no
        such page and for one that returns it on open, in
        :attr:`BrowserSession.control_url <web_router.BrowserSession.control_url>`.
        """
        return None

    async def _acontrol_url(self, session_id: str) -> str | None:
        """Async counterpart of :meth:`_control_url`."""
        return self._control_url(session_id)

    # -- What callers use ----------------------------------------------------

    def open(self, request: BrowserRequest) -> BrowserHandle:
        """Validate the request, start a session, and hand back a handle that releases it."""
        self.validate(request)
        return self._opened(request)

    async def aopen(self, request: BrowserRequest) -> AsyncBrowserHandle:
        """Async counterpart of :meth:`open`."""
        self.validate(request)
        return await self._aopened(request)

    def release(self, session_id: str) -> None:
        """End a session, wrapping any failure.

        Idempotent: a release that fails is not an error when the backend then
        reports the session in one of :attr:`release_ended_states` -- released
        before, expired on a timeout, or by default crashed -- because what the
        caller wanted is already true. In any other state the release error is
        raised.
        """
        try:
            self._release(session_id)
        except Exception as exc:
            if self._already_ended(session_id):
                return
            if isinstance(exc, WebRouterError):
                raise
            raise self._failure(exc) from exc

    async def arelease(self, session_id: str) -> None:
        """Async counterpart of :meth:`release`."""
        try:
            await self._arelease(session_id)
        except Exception as exc:
            if await self._aalready_ended(session_id):
                return
            if isinstance(exc, WebRouterError):
                raise
            raise self._failure(exc) from exc

    def status(self, session_id: str) -> BrowserState:
        """Ask the backend where a session is, wrapping any failure."""
        return self._guarded(lambda: self._fetch(session_id))

    async def astatus(self, session_id: str) -> BrowserState:
        """Async counterpart of :meth:`status`."""
        return await self._aguarded(self._afetch(session_id))

    def control_url(self, session_id: str) -> str | None:
        """Ask the backend for a page where a person can drive the session, wrapping any failure."""
        return self._guarded(lambda: self._control_url(session_id))

    async def acontrol_url(self, session_id: str) -> str | None:
        """Async counterpart of :meth:`control_url`."""
        return await self._aguarded(self._acontrol_url(session_id))

    def validate(self, request: BrowserRequest) -> None:
        """Raise for an option this backend does not map, a value of one it cannot serve, or a partner left unset.

        Raises:
            UnsupportedParameterError: for an option outside
                :attr:`supported_parameters`, and for a value of one this
                backend cannot serve -- a level outside :attr:`stealth_levels`,
                a country outside :attr:`proxy_countries` -- naming the value.
            MissingParameterError: for an option :attr:`co_requirements` pairs
                with one the request left unset. Missing rather than
                unsupported: the backend serves the option, and setting its
                partner is the fix.
        """
        self._reject_unmapped(request)
        refused = self.refused_values(request)
        if "stealth" in refused:
            levels = ", ".join(repr(level) for level in sorted(self.stealth_levels))
            raise UnsupportedParameterError(
                self.name,
                {"stealth"},
                values={"stealth": refused["stealth"]},
                detail=MSG_UNSUPPORTED_STEALTH.format(levels=levels),
            )
        if "proxy_country" in refused:
            raise UnsupportedParameterError(
                self.name,
                {"proxy_country"},
                values={"proxy_country": refused["proxy_country"]},
                detail=MSG_UNSUPPORTED_PROXY_COUNTRY,
            )
        unmet = self.unmet_requirements(request)
        if unmet:
            values = ", ".join(
                MSG_OPTION_VALUE.format(name=name, value=getattr(request, name)) for name in sorted(unmet)
            )
            raise MissingParameterError(
                self.name,
                frozenset().union(*unmet.values()),
                detail=MSG_BROWSER_CO_REQUIRED.format(values=values, options=", ".join(sorted(unmet))),
            )

    @classmethod
    def refused_values(cls, request: BrowserRequest) -> dict[str, Any]:
        """The options this request sets to a value this backend maps the option for but cannot serve.

        A stealth level outside :attr:`stealth_levels`, a country outside
        :attr:`proxy_countries`. On the class, as
        :meth:`BrowseProvider.missing_parameters` is, so a chooser can pass a
        backend over without a credential -- through the same check
        :meth:`validate` makes.
        """
        refused: dict[str, Any] = {}
        if (
            request.stealth is not None
            and "stealth" in cls.supported_parameters
            and request.stealth not in cls.stealth_levels
        ):
            refused["stealth"] = request.stealth
        if (
            request.proxy_country is not None
            and cls.proxy_countries is not None
            and request.proxy_country.upper() not in cls.proxy_countries
        ):
            refused["proxy_country"] = request.proxy_country
        return refused

    @classmethod
    def unmet_requirements(cls, request: BrowserRequest) -> dict[str, frozenset[str]]:
        """Each option this request sets that :attr:`co_requirements` pairs with one it left unset -> those partners.

        On the class, for the reason :meth:`refused_values` is.
        """
        unmet: dict[str, frozenset[str]] = {}
        for option, partners_by_value in cls.co_requirements.items():
            value = getattr(request, option)
            if value is None:
                continue
            partners = partners_by_value.get(value, frozenset())
            missing = frozenset(name for name in partners if getattr(request, name) is None)
            if missing:
                unmet[option] = missing
        return unmet

    # -- Helpers for subclasses ----------------------------------------------

    def state_of(self, reported: str, states: Mapping[str, BrowserState]) -> BrowserState:
        """Translate a backend's own session status through its table.

        A status the table does not list raises rather than being passed
        through, for the reason :meth:`ResearchProvider.state_of` gives: the
        backend has changed under us, and guessing "running" would keep paying
        for a session that may be long gone.
        """
        return _translate_state(self.name, reported, states, MSG_BROWSER_UNKNOWN_STATE)

    # -- Helpers -------------------------------------------------------------

    def _opened(self, request: BrowserRequest) -> BrowserHandle:
        """Start a session for a validated request, and wrap it in the handle that ends it.

        A provider implements :meth:`_open`, never this. The one exception is
        ``auto``, which opens nothing itself: its handle has to belong to the
        member that opened the session, so that the release goes to that
        member. It also receives the request before
        :data:`~web_router.constants.BROWSER_TIMEOUT` is filled in, because
        each member applies that default for itself.
        """
        resolved = self._with_default_timeout(request)
        return BrowserHandle(self, self._guarded(lambda: self._open(resolved)))

    async def _aopened(self, request: BrowserRequest) -> AsyncBrowserHandle:
        """Async counterpart of :meth:`_opened`."""
        resolved = self._with_default_timeout(request)
        return AsyncBrowserHandle(self, await self._aguarded(self._aopen(resolved)))

    def _with_default_timeout(self, request: BrowserRequest) -> BrowserRequest:
        """The request as sent: ``timeout`` filled in when unset and this backend maps it.

        Applied after validation, so it can never be the reason a call is
        rejected, and only where ``timeout`` is declared, so a backend with an
        idle limit alone is not handed an option it would have to refuse.
        """
        if request.timeout is not None or "timeout" not in self.supported_parameters:
            return request
        return request.model_copy(update={"timeout": BROWSER_TIMEOUT})

    def _already_ended(self, session_id: str) -> bool:
        try:
            return self._fetch(session_id) in self.release_ended_states
        except Exception:
            return False

    async def _aalready_ended(self, session_id: str) -> bool:
        try:
            return await self._afetch(session_id) in self.release_ended_states
        except Exception:
            return False

    def _failure(self, exc: Exception) -> ProviderError:
        return ProviderError(self.name, MSG_BROWSER_FAILED.format(error=exc), original=exc)

    def _guarded(self, call: Callable[[], _ResultT]) -> _ResultT:
        try:
            return call()
        except WebRouterError:
            raise
        except Exception as exc:
            raise self._failure(exc) from exc

    async def _aguarded(self, awaitable: Awaitable[_ResultT]) -> _ResultT:
        try:
            return await awaitable
        except WebRouterError:
            raise
        except Exception as exc:
            raise self._failure(exc) from exc


class _Handle:
    """What the sync and async handles share: the session, and the warning if it leaks."""

    def __init__(self, provider: BrowserProvider, session: BrowserSession) -> None:
        self._provider = provider
        self._session = session
        self._released = False
        self._asked_control_url: str | None = None
        self._control_url_asked = False

    @property
    def session(self) -> BrowserSession:
        """What the backend said about this session when it opened it."""
        return self._session

    @property
    def provider(self) -> str:
        """The backend serving it."""
        return self._provider.name

    @property
    def session_id(self) -> str:
        """The backend's own id for this session."""
        return self._session.session_id

    @property
    def cdp_url(self) -> str:
        """The Chrome DevTools Protocol endpoint, as a plain string to hand a driver.

        Plain on purpose, because asking for it by name is the one moment it is
        needed. It is a credential: do not log it.
        """
        return self._session.cdp_url.get_secret_value()

    @property
    def live_view_url(self) -> str | None:
        """A page showing the session as it runs, when the backend offers one that grants no control."""
        return self._session.live_view_url

    def _known_control_url(self) -> str | None:
        if self._session.control_url is not None:
            return self._session.control_url.get_secret_value()
        return self._asked_control_url

    def _must_ask_control_url(self) -> bool:
        # Asked once, answer or no answer; and never for a session already released,
        # which has no page left to drive.
        return self._session.control_url is None and not self._control_url_asked and not self._released

    @property
    def released(self) -> bool:
        """Whether this handle has ended its session."""
        return self._released

    def __del__(self) -> None:
        # Warn rather than release: a network call from a finalizer runs at
        # whatever moment the collector picks, on whatever thread, possibly with
        # the event loop already gone. Saying so is the most that is safe here.
        if not getattr(self, "_released", True):
            warnings.warn(
                MSG_BROWSER_LEAKED.format(provider=self.provider, session_id=self.session_id),
                ResourceWarning,
                # There is no caller to point at: a finalizer runs wherever
                # the collector happened to be.
                stacklevel=1,
            )

    def _warn_masked(self, error: WebRouterError) -> None:
        warnings.warn(
            MSG_BROWSER_RELEASE_MASKED.format(provider=self.provider, session_id=self.session_id, error=error),
            ResourceWarning,
            stacklevel=3,
        )

    def __repr__(self) -> str:
        # Provider and id only: the CDP URL is a credential, and a repr is what
        # ends up in logs and tracebacks.
        return f"<{type(self).__name__} provider={self.provider!r} session_id={self.session_id!r}>"


class BrowserHandle(_Handle):
    """An open browser session, and the thing that ends it.

    Returned by :meth:`WebRouter.open_browser`. Use it as a context manager and
    the session is released when the block ends, however it ends::

        with router.open_browser() as browser:
            page = playwright.chromium.connect_over_cdp(browser.cdp_url)

    Releasing twice is a no-op, and a handle collected while its session is
    still open warns with :class:`ResourceWarning` rather than failing silently.

    The handle also drives the session's page, the same way whichever backend
    serves it::

        with router.open_browser() as browser:
            browser.goto("https://news.ycombinator.com")
            page = browser.snapshot()          # each element tagged [ref=e1], [ref=e2], ...
            browser.click(ref="e14")           # or selector="a.morelink"
            png = browser.screenshot(full_page=True).png

    The actions need the ``browser`` extra. The first one connects Playwright to
    :attr:`cdp_url` and every later one reuses that connection -- several
    backends end a session when its CDP client disconnects -- until
    :meth:`release` closes it. They use Playwright's sync API, so they refuse
    to run inside an event loop; use :class:`AsyncBrowserHandle` there. Every
    action has a timeout, :data:`~web_router.constants.BROWSER_ACTION_TIMEOUT`
    seconds unless the call passes its own, and every failure raises a
    :class:`~web_router.exceptions.BrowserActionError`.

    Only :meth:`snapshot` takes accessibility snapshots of this page. The refs
    it hands out resolve against the latest one alone, so a driver of your own
    on :attr:`cdp_url` that calls ``aria_snapshot`` makes them all stale.
    """

    def __init__(self, provider: BrowserProvider, session: BrowserSession) -> None:
        super().__init__(provider, session)
        self._page: SyncPage | None = None

    def goto(self, url: str, *, wait_until: PageLoadState | None = None, timeout: float | None = None) -> PageState:
        """Load ``url`` in the session's page.

        ``wait_until`` is how far the page must get before this returns;
        unset, Playwright waits for ``load``. The result's ``status_code`` is
        ``None`` when the navigation produced no response.

        Raises:
            NavigationError: if the page could not be loaded.
        """
        return self._actions().goto(url, wait_until=wait_until, timeout=timeout)

    def snapshot(self, *, max_chars: int | None = None, timeout: float | None = None) -> PageSnapshot:
        """The page's accessibility tree, each element tagged with a ref to act on.

        The refs replace any this handle gave out before, and a navigation
        clears them. ``max_chars`` caps the tree: past it, the tree is cut at a
        line boundary and ``truncated`` is set, and the refs beyond the cut are
        not offered. Unset, there is no cap.
        """
        return self._actions().snapshot(max_chars=max_chars, timeout=timeout)

    def click(self, *, ref: str | None = None, selector: str | None = None, timeout: float | None = None) -> PageState:
        """Click an element, named by a ``ref`` from the latest snapshot or by a Playwright ``selector``.

        Pass exactly one of the two, or this raises ``ValueError``.

        Raises:
            StaleElementError: if ``ref`` is not in the latest snapshot, or its
                element has since been removed or re-rendered.
            ElementNotFoundError: if ``selector`` matched nothing in time.
        """
        return self._actions().click(ref=ref, selector=selector, timeout=timeout)

    def type(
        self,
        text: str,
        *,
        ref: str | None = None,
        selector: str | None = None,
        submit: bool = False,
        timeout: float | None = None,
    ) -> PageState:
        """Replace the contents of a field with ``text``, then press Enter if ``submit``.

        The field is named as :meth:`click` names an element, and fails the same ways.
        """
        return self._actions().type(text, ref=ref, selector=selector, submit=submit, timeout=timeout)

    def press(self, key: str) -> PageState:
        """Press a key on whatever has focus, by Playwright's key name: ``"Enter"``, ``"Control+A"``.

        Takes no timeout: a key press is dispatched, not waited on.
        """
        return self._actions().press(key)

    def screenshot(self, *, full_page: bool = False, timeout: float | None = None) -> Screenshot:
        """A PNG of the viewport, or of the whole scrollable page when ``full_page``."""
        return self._actions().screenshot(full_page=full_page, timeout=timeout)

    def read(self, *, format: PageFormat = "text", timeout: float | None = None) -> ExtractedDocument:
        """The page as it is now, in the document shape :meth:`WebRouter.extract` returns.

        ``text`` is the rendered text of the body; ``html`` the live DOM,
        scripts' changes included. Any other format raises ``ValueError``.
        :class:`~web_router.ExtractedDocument` has no provider field: the
        backend that served it is this handle's :attr:`provider`.
        """
        return self._actions().read(format=format, timeout=timeout)

    def release(self) -> None:
        """Close the page connection, then end the session. A no-op if this handle already has."""
        if self._released:
            return
        try:
            if self._page is not None:
                self._page.close()
        finally:
            self._provider.release(self.session_id)
        self._released = True

    def _actions(self) -> SyncPage:
        if self._page is None:
            self._page = SyncPage(self.provider, self.session_id, self.cdp_url, closed=self._released)
        return self._page

    def status(self) -> BrowserState:
        """Ask the backend where the session is now.

        A session this handle released is not asked about: it is ``released``.
        """
        if self._released:
            return "released"
        return self._provider.status(self.session_id)

    def control_url(self) -> str | None:
        """A page where a person can see and drive this session -- to log in to a site, say -- or ``None``.

        Hand it to the person who will use it, and to nobody else: whoever
        opens it controls the browser, and on some backends no account login
        stands in the way. Do not log it. A backend that returns the page on
        open answers from memory; Browserbase hands it out only on request, so
        the first call asks once and later calls reuse the answer. ``None``
        when the backend offers no such page, and on a released handle that
        never asked: the session has no page left to drive.

        A login typed there is kept for a later session only when the session
        was opened with ``profile=`` and ``persist_profile=True``, and saved
        when it is released.
        """
        if self._must_ask_control_url():
            self._asked_control_url = self._provider.control_url(self.session_id)
            self._control_url_asked = True
        return self._known_control_url()

    def __enter__(self) -> BrowserHandle:
        return self

    def __exit__(
        self,
        exc_type: builtins.type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the session. If the block raised, a failed release warns rather than replacing that error."""
        if exc is None:
            self.release()
            return
        try:
            self.release()
        except WebRouterError as error:
            # The block's own exception is the one the caller needs to see;
            # the session stays unreleased, so it is also warned about again
            # if the handle is collected.
            self._warn_masked(error)


class AsyncBrowserHandle(_Handle):
    """The async counterpart of :class:`BrowserHandle`, returned by :meth:`AsyncWebRouter.open_browser`.

    A separate class rather than one with both sets of methods, because what a
    caller does with the session differs as sharply: Playwright's sync API
    refuses to run inside an event loop, so a handle is for one world or the
    other::

        async with await router.open_browser() as browser:
            await browser.goto("https://example.com")
            page = await browser.snapshot()

    Its page actions are :class:`BrowserHandle`'s, awaited, over Playwright's
    async API, and follow the same rules.
    """

    def __init__(self, provider: BrowserProvider, session: BrowserSession) -> None:
        super().__init__(provider, session)
        self._page: AsyncPage | None = None

    async def goto(
        self, url: str, *, wait_until: PageLoadState | None = None, timeout: float | None = None
    ) -> PageState:
        """Load ``url`` in the session's page, as :meth:`BrowserHandle.goto` does."""
        return await self._actions().goto(url, wait_until=wait_until, timeout=timeout)

    async def snapshot(self, *, max_chars: int | None = None, timeout: float | None = None) -> PageSnapshot:
        """The page's accessibility tree with refs, as :meth:`BrowserHandle.snapshot` returns it."""
        return await self._actions().snapshot(max_chars=max_chars, timeout=timeout)

    async def click(
        self, *, ref: str | None = None, selector: str | None = None, timeout: float | None = None
    ) -> PageState:
        """Click an element, as :meth:`BrowserHandle.click` does."""
        return await self._actions().click(ref=ref, selector=selector, timeout=timeout)

    async def type(
        self,
        text: str,
        *,
        ref: str | None = None,
        selector: str | None = None,
        submit: bool = False,
        timeout: float | None = None,
    ) -> PageState:
        """Fill a field, as :meth:`BrowserHandle.type` does."""
        return await self._actions().type(text, ref=ref, selector=selector, submit=submit, timeout=timeout)

    async def press(self, key: str) -> PageState:
        """Press a key, as :meth:`BrowserHandle.press` does."""
        return await self._actions().press(key)

    async def screenshot(self, *, full_page: bool = False, timeout: float | None = None) -> Screenshot:
        """A PNG of the page, as :meth:`BrowserHandle.screenshot` takes it."""
        return await self._actions().screenshot(full_page=full_page, timeout=timeout)

    async def read(self, *, format: PageFormat = "text", timeout: float | None = None) -> ExtractedDocument:
        """The page's text or HTML, as :meth:`BrowserHandle.read` returns it."""
        return await self._actions().read(format=format, timeout=timeout)

    def on_connect(self, hook: Callable[[AsyncPlaywrightPage], Awaitable[None]]) -> None:
        """Run ``hook`` on the session's Playwright page when the first action connects to it.

        The one way in to that page before any action runs -- to install a
        request route, say, as the MCP server does to keep a model away from the
        session's own debugging endpoint. Call it before the first action.

        Raises:
            RuntimeError: if an action has already connected.
        """
        self._actions().set_connect_hook(hook)

    async def release(self) -> None:
        """Close the page connection, then end the session. A no-op if this handle already has."""
        if self._released:
            return
        try:
            if self._page is not None:
                await self._page.close()
        finally:
            await self._provider.arelease(self.session_id)
        self._released = True

    def _actions(self) -> AsyncPage:
        if self._page is None:
            self._page = AsyncPage(self.provider, self.session_id, self.cdp_url, closed=self._released)
        return self._page

    async def status(self) -> BrowserState:
        """Ask the backend where the session is now, as :meth:`BrowserHandle.status` does."""
        if self._released:
            return "released"
        return await self._provider.astatus(self.session_id)

    async def control_url(self) -> str | None:
        """A page where a person can drive this session, as :meth:`BrowserHandle.control_url` returns it."""
        if self._must_ask_control_url():
            self._asked_control_url = await self._provider.acontrol_url(self.session_id)
            self._control_url_asked = True
        return self._known_control_url()

    async def __aenter__(self) -> AsyncBrowserHandle:
        return self

    async def __aexit__(
        self,
        exc_type: builtins.type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the session, as :meth:`BrowserHandle.__exit__` does."""
        if exc is None:
            await self.release()
            return
        try:
            await self.release()
        except WebRouterError as error:
            self._warn_masked(error)


def _translate_state(provider: str, reported: str, states: Mapping[str, _StateT], message: str) -> _StateT:
    try:
        return states[reported]
    except KeyError as exc:
        raise ProviderError(provider, message.format(state=reported)) from exc


def _deadline_for(timeout: float | None) -> float | None:
    """The monotonic instant a call with this budget runs out, or ``None`` for no budget."""
    return None if timeout is None else time.monotonic() + timeout


def _passed(deadline: float | None) -> bool:
    """Whether a call with this deadline has run out of time. Never, with no deadline."""
    return deadline is not None and time.monotonic() >= deadline


def _is_timeout(error: ProviderError) -> bool:
    """Whether a provider's failure was a request that timed out, anywhere down its chain of causes."""
    seen: set[int] = set()
    current: BaseException | None = error.original or error.__cause__
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if any(cls.__name__.endswith(JOB_TIMEOUT_ERROR_SUFFIXES) for cls in type(current).__mro__):
            return True
        current = current.__cause__ or current.__context__
    return False
