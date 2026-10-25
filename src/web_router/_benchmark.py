"""The fan-out behind :meth:`~web_router.WebRouter.benchmark` and :meth:`~web_router.WebRouter.benchmark_extract`.

A comparison is generic in the method it fans out: the provider it calls,
the response that call returns, and the row the report keeps per provider.
What the two methods share is here -- the members resolved before any row
starts, the threads or the tasks, the deadline and the rows that outlive
it -- and each router method passes in its own call and row builder. Private
to the routers: nothing here is a public name.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any, TypeVar

from web_router.constants import MSG_BENCHMARK_ROW_FAILED
from web_router.exceptions import BenchmarkTimeoutError, ProviderError, WebRouterError
from web_router.providers.base import ExtractProvider, SearchProvider
from web_router.types import BenchmarkRun, ExtractBenchmarkRun, ExtractResponse, Provider, SearchResponse, provider_name

_MemberT = TypeVar("_MemberT", bound="SearchProvider | ExtractProvider")
_AnswerT = TypeVar("_AnswerT", bound="SearchResponse | ExtractResponse")
_RunT = TypeVar("_RunT", bound="BenchmarkRun | ExtractBenchmarkRun")


class Stragglers:
    """Rows the sync fan-out gave up on whose calls still run.

    The router's ``close()`` joins them, so it never shuts a provider under a
    live call; each is dropped the moment it finishes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._futures: set[Future[Any]] = set()

    def track(self, future: Future[Any]) -> None:
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._forget)

    def _forget(self, future: Future[Any]) -> None:
        with self._lock:
            self._futures.discard(future)

    def join(self) -> None:
        """Block until every call still tracked has finished -- bounded by each provider's own HTTP timeout."""
        with self._lock:
            pending = tuple(self._futures)
        if pending:
            wait(pending)


def members_of(
    providers: Sequence[str | Provider | _MemberT] | None,
    *,
    default: Callable[[], list[str]],
    build: Callable[[str | Provider | _MemberT], _MemberT],
    none_named: str,
) -> list[tuple[str, _MemberT | WebRouterError]]:
    """The rows to run, as ``(row name, the provider or what stopped it)``, each name once, in the order asked.

    Resolved on the caller's thread and before any row starts, for two
    reasons: a backend whose extra is not installed or whose key is missing
    is that row's failure rather than the comparison's, and a provider built
    inside a worker after the router was closed would be cached by a router
    that will never close it. ``build`` is the method's own getter, so the
    provider is the one a plain call would use.
    """
    members: list[tuple[str, _MemberT | WebRouterError]] = []
    seen: set[str] = set()
    for item in default() if providers is None else providers:
        name = provider_name(item) if isinstance(item, (str, Provider)) else item.name
        if name in seen:
            continue
        seen.add(name)
        try:
            members.append((name, build(item)))
        except WebRouterError as exc:
            members.append((name, exc))
        except Exception as exc:
            members.append((name, _unexpected(name, exc)))
    if not members:
        raise ValueError(none_named)
    return members


def _unexpected(name: str, exc: Exception) -> ProviderError:
    """A failure the public path did not wrap -- a provider bug, a caller's own provider -- as the row's error.

    The public methods wrap every backend failure, so nothing should reach
    here; when something does, the report still comes back with this row
    saying what raised, as its docstring promises.
    """
    return ProviderError(name, MSG_BENCHMARK_ROW_FAILED.format(error=exc), original=exc)


def fan_out(
    members: list[tuple[str, _MemberT | WebRouterError]],
    call: Callable[[_MemberT], _AnswerT],
    row_of: Callable[[str, float | None, _AnswerT | WebRouterError], _RunT],
    timeout: float | None,
    *,
    stragglers: Stragglers,
) -> list[_RunT]:
    """Every member's row at once, each timed around its own ``call``, the whole bounded by ``timeout``.

    ``row_of`` builds the method's own row from a name, when its call
    started, and what it got or raised -- ``BenchmarkRun.of`` or
    ``ExtractBenchmarkRun.of``. A row still unanswered at the
    deadline is reported as timed out and its call left to finish on its
    thread, tracked in ``stragglers`` until it does.
    """
    started = time.perf_counter()
    # A member that could not be built is its row already, with nothing to time.
    runs = {name: row_of(name, None, member) for name, member in members if isinstance(member, WebRouterError)}
    futures = _start(members, call, row_of)
    wait(futures.values(), timeout=timeout)
    runs.update(_collect(futures, row_of, started, timeout, stragglers))
    return [runs[name] for name, _ in members]


def _start(
    members: list[tuple[str, _MemberT | WebRouterError]],
    call: Callable[[_MemberT], _AnswerT],
    row_of: Callable[[str, float | None, _AnswerT | WebRouterError], _RunT],
) -> dict[str, Future[_RunT]]:
    """Each buildable member's row started on a thread of its own, by name."""

    def row(name: str, provider: _MemberT) -> _RunT:
        own = time.perf_counter()
        try:
            outcome: _AnswerT | WebRouterError = call(provider)
        except WebRouterError as exc:
            outcome = exc
        except Exception as exc:
            outcome = _unexpected(name, exc)
        return row_of(name, own, outcome)

    live = [(name, member) for name, member in members if not isinstance(member, WebRouterError)]
    # Not a `with` block: leaving one joins every thread, which would hold
    # the report until the slowest backend answered and make the timeout
    # a fiction. A row given up on finishes on its own and is discarded;
    # its provider was built before it started, so the router closes it.
    pool = ThreadPoolExecutor(max_workers=max(1, len(live)))
    try:
        return {name: pool.submit(row, name, member) for name, member in live}
    finally:
        pool.shutdown(wait=False)


def _collect(
    futures: dict[str, Future[_RunT]],
    row_of: Callable[[str, float | None, Any], _RunT],
    started: float,
    timeout: float | None,
    stragglers: Stragglers,
) -> dict[str, _RunT]:
    """Each started row as it stands once the deadline has passed: its result, or a timeout."""
    runs: dict[str, _RunT] = {}
    for name, future in futures.items():
        if future.done():
            runs[name] = future.result()
            continue
        # A row given up on reports how long it was waited for. Its call
        # keeps running, on a provider this router will close, so close()
        # waits for it -- tracked here, dropped the moment it finishes.
        runs[name] = row_of(name, started, BenchmarkTimeoutError(name, timeout or 0.0))
        stragglers.track(future)
    return runs


async def afan_out(
    members: list[tuple[str, _MemberT | WebRouterError]],
    call: Callable[[_MemberT], Awaitable[_AnswerT]],
    row_of: Callable[[str, float | None, _AnswerT | WebRouterError], _RunT],
    timeout: float | None,
) -> list[_RunT]:
    """The async twin of :func:`fan_out`: the rows gathered on the loop under one deadline, late ones cancelled."""
    deadline = None if timeout is None else time.perf_counter() + timeout

    async def row(name: str, member: _MemberT | WebRouterError) -> _RunT:
        if isinstance(member, WebRouterError):
            return row_of(name, None, member)
        own = time.perf_counter()
        # One deadline for the whole comparison: a row that starts late
        # gets what is left of it, not a fresh timeout of its own.
        remaining = None if deadline is None else max(0.0, deadline - own)
        try:
            outcome: _AnswerT | WebRouterError = await asyncio.wait_for(call(member), remaining)
        except asyncio.TimeoutError:
            outcome = BenchmarkTimeoutError(name, timeout or 0.0)
        except WebRouterError as exc:
            outcome = exc
        except Exception as exc:
            outcome = _unexpected(name, exc)
        return row_of(name, own, outcome)

    return list(await asyncio.gather(*(row(name, member) for name, member in members)))
