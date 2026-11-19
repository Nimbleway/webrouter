"""One attempt of an ``auto`` walk: the request it sends in its vendor mode, its price, and what its failure means.

A router's ``attempts`` may name one backend in several vendor modes
(:mod:`web_router.routing.merge_attempts`). Each attempt in a mode is sent a
copy of the call's request in that mode: the mode replaces the ``search_depth``
and ``effort`` a router set, and every other option still applies. Its answer
is priced at that mode. Its failure is read by
:mod:`web_router.routing.failure_kind`: a refused mode is taken back, as never
charged, and the backend's next attempt runs; ``local`` and ``target`` raise;
anything else stops the backend for the call, so its later attempts are passed
over, each recorded as ``backend_failed``. Cooldowns stay keyed by backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from web_router.exceptions import WebRouterError
from web_router.exceptions_typed import InvalidParameterError
from web_router.providers._chain import RECOVERABLE, nothing_started
from web_router.providers._modes import PROBED
from web_router.providers._pinning import costed, pinned_request
from web_router.routing.attempts import Attempt, admits, current_route, failed, noted, refunded
from web_router.routing.entry_names import entry_name
from web_router.routing.failure_kind import BACKEND_FAILED, mode_refused
from web_router.types import SearchRequest

if TYPE_CHECKING:
    from web_router.providers._attempt_loop import Walk

Failures = dict[str, WebRouterError]


@dataclass
class Tried:
    """What a walk has met so far: each attempt's failure, the refusals, the first empty answer, the stopped."""

    failures: Failures = field(default_factory=dict)
    refused: list[WebRouterError] = field(default_factory=list)
    held: Any = None
    stopped: set[int] = field(default_factory=set)
    """The chain positions whose backend failed in this call: that position's later attempts are passed over, while
    another position of the same backend (a keyed entry after a keyless one) still runs."""

    def hold(self, response: object) -> None:
        # Not `held or response`: an empty response is falsy, having no length.
        self.held = response if self.held is None else self.held


def in_mode(member: Any, request: Any, mode: str | None) -> Any:
    """``request`` in ``mode`` for ``member``, the router's ``search_depth`` and ``effort`` replaced; with no mode,
    ``request`` itself."""
    if mode is None or not isinstance(request, SearchRequest):
        return request
    route = current_route()
    replaced = dict.fromkeys(key for key in PROBED if route is not None and key in route.router_params)
    return pinned_request(member, request.model_copy(update=replaced), mode)


def prepared(walk: Walk, tried: Tried, position: int, name: str, mode: str | None, request: Any) -> Attempt | None:
    """The attempt at ``position`` in ``mode``, admitted by the call's hook; ``None`` when it is passed over.

    Passed over: its backend stopped earlier in the call, its mode refused before anything is sent, a member
    that cannot be built (a failure as any other), or the hook said no.
    """
    if position in tried.stopped:
        noted(walk.tool, entry_name(name, mode), BACKEND_FAILED)
        return None
    if mode is not None:
        try:
            request = in_mode(walk.member(position), request, mode)
        except InvalidParameterError as exc:  # the request sets what the mode writes: this mode only
            failed(tried.failures, walk.tool, entry_name(name, mode), exc)
            return None
        except RECOVERABLE as exc:
            failure(walk, tried, position, exc, name, mode)
            return None
    attempt = Attempt(tool=walk.tool, provider=name, request=request, mode=mode)
    return attempt if admits(attempt) else None


def priced(response: Any, member: Any, attempt: Attempt) -> Any:
    """``response`` carrying its mode's estimated price when it states none; any other answer as it is."""
    request = attempt.request
    return costed(response, member, request) if attempt.mode and isinstance(request, SearchRequest) else response


def failure(
    walk: Walk,
    tried: Tried,
    position: int,
    exc: WebRouterError,
    name: str,
    mode: str | None,
    sent: Attempt | None = None,
) -> None:
    """Record ``name``'s failure in ``mode`` and move on; raise it when moving on could bill twice or would not help.

    A mode the backend refused, ``sent``, is taken back; any other failure stops the backend at ``position``.
    """
    if not walk.hops and not nothing_started(exc):
        raise exc
    if sent is not None and sent.mode is not None and mode_refused(exc):
        refunded(sent)
    else:
        tried.stopped.add(position)
    failed(tried.failures, walk.tool, entry_name(name, mode), exc, walk.built.get(position))


def refused(walk: Walk, tried: Tried, attempt: Attempt, exc: WebRouterError) -> None:
    """Record an option ``attempt``'s member refused before sending, which does not count against it."""
    tried.refused.append(exc)
    noted(walk.tool, entry_name(attempt.provider, attempt.mode), exc)
