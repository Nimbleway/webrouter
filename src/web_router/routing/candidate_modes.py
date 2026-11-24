"""Candidates per vendor mode, for :mod:`web_router.routing.candidates`, when the router opts in with ``route_modes``
or names modes of its own with ``extra_modes``.

Experimental. With ``route_modes``, each search member's ``offers`` (:func:`~web_router.providers._modes.offers`)
become candidates of their own, such as ``search:exa:exa-instant``, right after their backend's, so a mode keeps its
backend's place in the static order and in the cost order. A mode is held to the request as pinning it would be: one
the member refuses -- a ``search_depth`` the caller set that it overwrites, a ``provider_options`` key its wire writes
-- is dropped with why, never raised. Each is priced on its own request, so a budget prices and drops it as it does a
backend. Nothing is cached: what a mode accepts and what it costs depend on the request.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from web_router.constants import PROVIDER_AUTO
from web_router.constants_candidates import AUTO_GETTERS, MSG_DROPPED_REFUSES_MODE
from web_router.exceptions import WebRouterError
from web_router.exceptions_typed import InvalidParameterError
from web_router.providers._modes import SEARCH, offers
from web_router.providers._pinning import pinned_request
from web_router.types import NormalizedRequest, SearchRequest

ModeEntry = tuple[str, NormalizedRequest | str]
"""One mode a member offers: its canonical id and the request in that mode, or why the member refuses it."""


def mode_requests(
    router: Any,
    tool: str,
    request: NormalizedRequest,
    name: str,
    *,
    offered: bool = True,
    extra: Mapping[str, Sequence[str]] = MappingProxyType({}),
) -> list[ModeEntry]:
    """Each mode member ``name`` of ``tool``'s ``auto`` offers for ``request``, in its ``offers`` order, when
    ``offered``; then the modes ``extra`` names for it, such as a tier at a depth, each once.

    None for a method other than search, a request already in a mode, or a member pinned to one. A mode that
    sends what the request already sends -- a depth it asks for, a backend's default depth -- is the backend's own
    candidate, so it is not offered again.
    """
    if tool != SEARCH or not isinstance(request, SearchRequest) or request.mode:
        return []
    owner = _member(router, tool, name)
    if owner is None or getattr(owner, "pinned", None):
        return []
    default = _payload(owner, request)
    if default is None:  # no payload to merge a mode into, or the request fails for this backend in any mode
        return []
    entries: list[ModeEntry] = []
    for wanted in (*(offers(owner) if offered else ()), *extra.get(name, ())):
        try:
            moded = pinned_request(owner, request, wanted)
            payload = owner._build_payload(moded)  # the mode merged and its refusals held, as the call sends it
        except InvalidParameterError as exc:
            entries.append((wanted, MSG_DROPPED_REFUSES_MODE.format(why=exc)))
            continue
        except WebRouterError as exc:  # the backend's own refusal: named by class, since its text may quote the query
            entries.append((wanted, MSG_DROPPED_REFUSES_MODE.format(why=type(exc).__name__)))
            continue
        if moded.mode and payload != default:  # one named twice is placed once, by the candidate builder
            entries.append((moded.mode, moded))
    return entries


def _payload(owner: Any, request: SearchRequest) -> dict[str, Any] | None:
    """The payload ``owner`` builds for ``request``, never sent; ``None`` when it builds none -- a backend of the
    caller's own may call its API another way -- or refuses the request itself, such as a query over its limit. The
    call reports a refusal when it runs, so building candidates does not raise on it; a bug in the builder does."""
    build = getattr(owner, "_build_payload", None)
    if build is None:
        return None
    try:
        return dict(build(request))
    except WebRouterError:
        return None


def _member(router: Any, tool: str, name: str) -> Any:
    """The built member named ``name`` in ``tool``'s ``auto``, as ``auto`` built and cached it; ``None`` if none."""
    auto = getattr(router, AUTO_GETTERS[tool])(PROVIDER_AUTO)
    for position, (entry, _) in enumerate(auto._chain):
        if entry == name:
            try:
                return auto._member(position)
            except Exception:  # an earlier entry of this name that cannot be built: eligibility named it, with why
                continue
    return None
